"""Image/audio/deep-scan pipeline domain implementation.

Phase 39 moves the two large image/deep-scan orchestration functions out of
legacy.py while preserving their historical globals through a compatibility
namespace snapshot. The function bodies are intentionally mechanical copies;
output names, report formats, and download behavior are unchanged.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import mimetypes
import os
import pathlib
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from itertools import islice
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

import requests

from ..app_info import DEFAULT_MAX_WORKERS, DEFAULT_WAIT_TIME
from ..constants.assets import _SOUNDCLOUD_URL_RE, _YOUTUBE_ID_RE, _YOUTUBE_URL_RE, AUDIO_FIELDS, IMAGE_FIELDS
from ..core.atomic_io import atomic_write_bytes
from ..core.cancellation import (
    _cancel_aware_sleep,
    _emit_progress_event,
    _raise_if_cancelled,
)
from ..core.concurrency import iter_bounded_results
from ..core.embed_io import EmbeddedMedia, project_chunks, value_chunks
from ..core.media_io import MEDIA_CHUNK_BYTES, FilePayload, ResponseBodySizeError, atomic_copy_file, spool_chunks
from ..core.paths import _safe_archive_join, _safe_join, _safe_rel_path
from ..core.progress import DownloadCancelledError
from ..diagnostics.reports import write_asset_failure_summary
from ..integrations.ai_calls import _ai_analyze_js_for_assets
from ..integrations.ai_core import (
    AIUsageBudget,
    _ai_is_available,
    _ai_mode_allows,
    _get_ai_provider,
    _normalize_ai_mode,
    _normalize_ai_provider,
    _ssrf_block_cross_origin,
)
from ..integrations.discord_attachments import (
    REFRESHABLE_HTTP_STATUSES,
    DiscordAttachmentClient,
    DiscordAttachmentError,
    discord_recovery_enabled,
    is_discord_attachment_url,
    resolve_discord_bot_token,
)
from ..integrations.gallery_dl import _fetch_via_gallery_dl, _is_gallery_dl_site
from ..integrations.plugins import run_asset_scanner_plugins
from ..logging_setup import logger
from ..network.browser import BrowserFetchSession, _fetch_headless, _make_cookie_session
from ..network.budget import HTTP_BUDGET
from ..network.fetch import fetch_response
from ..network.retry_policy import caller_status_retry_scope, retry_after_seconds
from ..network.throttle import (
    _domain_record_failure,
    _domain_record_success,
    _domain_throttle,
    _throttle_bandwidth,
)
from ..network.vpn import vpn_requirement_satisfied
from ..project.mediawiki import mediawiki_image_url
from ..runtime import state as runtime_state
from ..storage.cache import _cache_get, _cache_get_file, _cache_put, _cache_put_file
from .asset_scan import (
    _check_image_dedup,
    _check_image_dedup_digest,
    _deep_scan_project_assets,
    _extract_image_references,
    _GeneratedEntryImageIndex,
    _is_probable_raw_cdn_asset,
    _safe_response_text,
    _scan_file_for_assets,
)
from .audio_download import _download_youtube_audio, _make_ytdlp_hook
from .audio_reports import (
    _find_ffmpeg,
    _patch_youtube_refs_in_json,
    _write_failed_images_log,
    _write_youtube_skip_log,
)
from .headers import get_headers_for_url

# Keep the historical opt-out patch point; the live user switch is read at use.
_SELENIUM_ENABLED = True

_DEEP_SCAN_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg", ".ico", ".tif", ".tiff",
}
_DEEP_SCAN_AUDIO_EXTENSIONS = {
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".opus", ".flac", ".weba",
}
_DEEP_SCAN_FONT_EXTENSIONS = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
_DEEP_SCAN_TEXT_EXTENSIONS = {".css", ".js", ".mjs", ".cjs", ".json", ".txt", ".html", ".htm"}

# These boundaries execute optional, independently implemented backends.  Keep
# them broad so one unavailable backend cannot abort the ordered fallback chain;
# every use reports the failure and cancellation is handled before the boundary.
_OPTIONAL_HTTP2_ERRORS = (Exception,)
_OPTIONAL_AI_ERRORS = (Exception,)
_ASSET_SCANNER_PLUGIN_ERRORS = (Exception,)

_RESPONSE_CLEANUP_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    requests.RequestException,
)
_NETWORK_OPERATION_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    requests.RequestException,
)
_FILE_OPERATION_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def _deep_scan_content_extension(url: str, content: bytes | None = None) -> str:
    """Best-effort extension for external assets whose CDN URL has none.

    A number of image CDNs use paths such as ``/avatarhd`` or append a
    transformation suffix after the real filename.  Keeping those paths as
    directories is noisy, while saving them without an extension makes local
    preview servers serve them with the wrong MIME type.  Prefer the URL/query
    hint and fall back to a small magic-byte check for the common formats.
    """
    try:
        parsed = urlparse(str(url))
        path_ext = os.path.splitext(parsed.path.rstrip("/"))[1].lower()
        if path_ext in (_DEEP_SCAN_IMAGE_EXTENSIONS | _DEEP_SCAN_AUDIO_EXTENSIONS | _DEEP_SCAN_FONT_EXTENSIONS | _DEEP_SCAN_TEXT_EXTENSIONS):
            return path_ext
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() not in {"format", "fm", "ext", "type"}:
                continue
            hint = os.path.splitext(value.split("/", 1)[-1])[1].lower()
            if hint in (_DEEP_SCAN_IMAGE_EXTENSIONS | _DEEP_SCAN_AUDIO_EXTENSIONS):
                return hint
            if value.lower().lstrip(".") in {ext[1:] for ext in _DEEP_SCAN_IMAGE_EXTENSIONS | _DEEP_SCAN_AUDIO_EXTENSIONS}:
                return "." + value.lower().lstrip(".")
    except (TypeError, ValueError):
        pass

    raw = bytes(content or b"")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return ".webp"
    if len(raw) >= 12 and raw[4:12] in {b"ftypavif", b"ftypavis"}:
        return ".avif"
    if raw.startswith(b"BM"):
        return ".bmp"
    if raw.startswith((b"ID3", b"\xff\xfb")):
        return ".mp3"
    if raw.lstrip().startswith((b"<svg", b"<?xml")):
        return ".svg"
    return ""


def _deep_scan_external_rel_path(url: str, content: bytes | None = None) -> str:
    """Return a flat, stable path for a cross-origin deep-scan asset."""
    parsed = urlparse(str(url))
    path_name = os.path.basename(parsed.path.rstrip("/")) or "asset"
    try:
        from .package import clean_url_path_component
        path_name = clean_url_path_component(path_name)
    except (ImportError, TypeError, ValueError):
        path_name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", path_name).strip(". ") or "asset"

    extension = _deep_scan_content_extension(url, content)
    stem, existing_ext = os.path.splitext(path_name)
    if existing_ext.lower() not in (_DEEP_SCAN_IMAGE_EXTENSIONS | _DEEP_SCAN_AUDIO_EXTENSIONS | _DEEP_SCAN_FONT_EXTENSIONS | _DEEP_SCAN_TEXT_EXTENSIONS):
        extension = extension or ".bin"
        stem = path_name
    else:
        extension = existing_ext

    # The short URL digest keeps same-named files from different CDNs and
    # meaningful query variants distinct without recreating the CDN directory
    # tree (which was the source of the huge hash-folder output).
    digest = hashlib.sha1(
        str(url).encode("utf-8", "replace"), usedforsecurity=False
    ).hexdigest()[:10]
    filename = f"{stem[:120]}_{digest}{extension}"

    lower_ext = extension.lower()
    if lower_ext in _DEEP_SCAN_IMAGE_EXTENSIONS:
        kind = "images"
    elif lower_ext in _DEEP_SCAN_AUDIO_EXTENSIONS:
        kind = "audio"
    elif lower_ext in _DEEP_SCAN_FONT_EXTENSIONS:
        kind = "fonts"
    elif lower_ext in _DEEP_SCAN_TEXT_EXTENSIONS:
        kind = "assets"
    else:
        kind = "assets"
    return _safe_rel_path(quote(f"external/{kind}/{filename}", safe="/"))


def _asset_is_error_document(
    mime: str,
    content: bytes,
    *,
    binary_asset: bool = True,
) -> bool:
    """Reject HTML/error documents while preserving valid deep-scan JSON.

    Image/audio downloads must reject any JSON response because it cannot be
    the requested binary media.  The website deep scanner also handles real
    JSON assets (notably ``project.json``), so it opts out of that MIME-only
    rejection and still rejects HTML plus obvious JSON error payloads.
    """
    content_type = (mime or "").split(";", 1)[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        return True
    if binary_asset and content_type == "application/json":
        return True
    prefix = bytes(content or b"")[:512].lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return True
    return prefix.startswith((b'{"error', b'{"errors'))


def _media_content_extension(url: str, mime: str, content: bytes) -> str:
    """Choose a media extension from bytes first, then MIME/URL hints.

    CDN responses can negotiate AVIF while retaining an old ``image/jpeg``
    cache MIME or exposing no useful filename.  The file signature is the
    authoritative signal for common image/audio formats; MIME and URL are
    fallbacks for formats without a small signature probe.
    """
    magic_ext = _deep_scan_content_extension("", content)
    if magic_ext:
        return magic_ext
    mime_ext = mimetypes.guess_extension((mime or "").split(";", 1)[0].strip()) or ""
    if mime_ext in (".jpe", ".jpeg"):
        mime_ext = ".jpg"
    if mime_ext:
        return mime_ext
    return _deep_scan_content_extension(url, None)


def process_images(
    input_str: str,
    base_url: str,
    embed: bool = False,
    download: bool = False,
    temp_folder: str | None = None,
    wait_time: int = DEFAULT_WAIT_TIME,
    max_workers: int = DEFAULT_MAX_WORKERS,
    output_dir: str = "",
    source_url: str = "",
    embed_audio: bool = False,
    site_folder: str = "",
    embed_writer=None,
) -> tuple[str, str, set[str]]:
    """Localize media using run-owned files.

    With embed_writer, consume the embedded JSON chunks inside this call and
    return an empty first string. Without it, preserve the string-returning API.
    """
    kwargs = {"embed": embed, "download": download, "temp_folder": temp_folder,
              "wait_time": wait_time, "max_workers": max_workers, "output_dir": output_dir,
              "source_url": source_url, "embed_audio": embed_audio, "site_folder": site_folder,
              "embed_writer": embed_writer}
    if download and not temp_folder:
        raise ValueError("temp_folder required when download=True")
    if download or embed:
        _raise_if_cancelled()
        if temp_folder:
            os.makedirs(temp_folder, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".cyoa-media-", dir=temp_folder) as spool:
            return _process_images(input_str, base_url, **kwargs, _spool=spool)
    return _process_images(input_str, base_url, **kwargs)


def _process_images(
    input_str: str,
    base_url: str,
    embed: bool = False,
    download: bool = False,
    temp_folder: str | None = None,
    wait_time: int = DEFAULT_WAIT_TIME,
    max_workers: int = DEFAULT_MAX_WORKERS,
    output_dir: str = "",
    source_url: str = "",
    embed_audio: bool = False,
    site_folder: str = "",   # ICC mode: check if images already exist here
    _spool: str | None = None,
    embed_writer=None,
) -> tuple[str, str, set[str]]:
    """
    Download image AND audio assets referenced in a project.json string.

    Image fields (IMAGE_FIELDS): image, backgroundImage, rowBackgroundImage,
        objectBackgroundImage, defaultImage — all downloaded regardless of origin.

    Audio fields (AUDIO_FIELDS): audio, audioSrc, backgroundMusic, etc.
        - Direct mp3/ogg/wav URLs → downloaded.
        - YouTube URLs/IDs        → kept as-is (cannot go offline), logged.

    embed_audio: if True, embed downloaded audio as data:audio/mpeg;base64,...
                 in the embed_str output (works without a server). Ignored if
                 the file exceeds 10 MB (too large to inline safely).

    Failed images → original URL kept in JSON (viewer shows broken image or blank).
    Returns (embed_str, download_str).
    """
    _raise_if_cancelled()
    data_uri_re = re.compile(r"^data:(?:image|audio|application)/[a-zA-Z0-9.+-]+;base64,")

    if download and not temp_folder:
        raise ValueError("temp_folder required when download=True")
    if download:
        images_folder = os.path.join(temp_folder, "images")
        audio_folder  = os.path.join(temp_folder, "audio")
        # NOTE: folders are created on-demand when first file is saved,
        # not here — so empty folders are never left behind.

    # ── Phase A: JSON-aware deep scan (handles bgmId+useAudioURL, nested sfx) ──
    # Reports belong beside the project payload, not beside the process-wide
    # output root. ICC/website mode stages bytes in temp_folder but has one
    # site_folder per CYOA, so batch reports must follow site_folder.
    report_dir = os.path.abspath(site_folder or output_dir or temp_folder or os.getcwd())

    deep_images, deep_audio, deep_youtube = _deep_scan_project_assets(input_str, base_url)

    # ── Phase B: Regex scan as fallback/supplement ──────────────────
    # Catches cases where JSON parsing fails (truncated payload, embedded JS, etc.)
    all_fields   = IMAGE_FIELDS + AUDIO_FIELDS
    field_group  = "|".join(re.escape(f) for f in all_fields)
    pattern      = rf'"({field_group})"\s*:\s*"([^"]+)"'
    image_fields_lower = {f.lower() for f in IMAGE_FIELDS}

    image_paths:   set[str] = set(deep_images)
    audio_paths:   set[str] = set(deep_audio)
    youtube_paths: set[str] = set(deep_youtube)
    image_field_aliases: dict[str, str] = {}

    for m in re.finditer(pattern, input_str, flags=re.IGNORECASE):
        field = m.group(1)
        path  = m.group(2)
        # Canonicalize JSON-escaped slashes. The escaped
        # form ("img\/x.png") was collected as a SEPARATE asset next to the
        # unescaped twin from the deep JSON walk: its fetch always fails on
        # real hosts, burning 4 retries + the headless fallback per asset and
        # polluting failed_images. make_embed/make_download below use the same
        # canonical key, so the rewrite still hits the escaped occurrence.
        if "\\/" in path:
            path = path.replace("\\/", "/")
        if data_uri_re.match(path):
            continue
        if _YOUTUBE_URL_RE.search(path):
            youtube_paths.add(path)
        elif field.lower() in image_fields_lower:
            # A malformed project can still reach this regex fallback. Do not
            # enqueue an entire HTML/text value as one image URL.
            references = _extract_image_references(path, allow_bare_path=True)
            image_paths.update(references)
            if len(references) == 1 and not any(char in path for char in "<>"):
                reference = next(iter(references))
                if reference != path and reference.startswith(("http://", "https://")):
                    image_field_aliases[path] = reference
        else:
            audio_paths.add(path)

    # Remove data URIs and blanks that slipped through
    for s in (image_paths, audio_paths, youtube_paths):
        s.discard("")
        to_remove = {p for p in s if data_uri_re.match(p)}
        s -= to_remove

    # The structured scan can discover the same YouTube/SoundCloud reference
    # twice when a malformed project stores it in an image-like field: once as
    # media audio and once as an image candidate. Without disjoint sets the
    # track is successfully downloaded by yt-dlp and then fetched again as an
    # image, causing repeated HTML/content-type retries and a several-minute
    # headless-browser timeout per track.
    for media_set in (image_paths, audio_paths):
        for candidate in list(media_set):
            value = str(candidate or "").strip()
            if _YOUTUBE_URL_RE.search(value) or _SOUNDCLOUD_URL_RE.search(value):
                youtube_paths.add(value)
                media_set.discard(candidate)
            elif _YOUTUBE_ID_RE.fullmatch(value):
                youtube_paths.add(f"https://www.youtube.com/watch?v={value}")
                media_set.discard(candidate)

    summary_total = len(image_paths) + len(audio_paths) + len(youtube_paths)

    if not image_paths and not audio_paths and not youtube_paths:
        logger.info("No external images or audio found.")
        if embed and embed_writer is not None:
            embed_writer(value_chunks(input_str))
            return "", input_str, set()
        return input_str, input_str, set()

    # Log summary
    logger.info(
        f"Assets found: {len(image_paths)} image(s), "
        f"{len(audio_paths)} direct audio file(s), "
        f"{len(youtube_paths)} YouTube reference(s) (kept as-is)."
    )
    _yt_local: dict[str, str] = {}   # yt-dlp downloaded files: local_rel → local_rel

    if youtube_paths:
        yt_audio_dir = temp_folder if temp_folder else output_dir
        yt_map = _download_youtube_audio(
            sorted(youtube_paths), yt_audio_dir, source_url=source_url,
            log_dir=report_dir,
        )
        if yt_map:
            input_str = _patch_youtube_refs_in_json(input_str, yt_map)
            youtube_paths -= set(yt_map.keys())
            _yt_local = yt_map  # track for pre-population below

    all_downloadable = image_paths | audio_paths
    if all_downloadable:
        ext_count   = sum(1 for p in all_downloadable if p.startswith(("http://", "https://")))
        local_count = len(all_downloadable) - ext_count
        logger.info(
            f"Downloading {len(all_downloadable)} asset(s) "
            f"({local_count} local, {ext_count} external) "
            f"with {max_workers} threads…"
        )

    # ── Fetch all downloadable assets in parallel ──────────────────
    # cache: original_path → (content | None, mime, resolved_url, error_str)
    fetch_cache: dict[str, tuple[bytes | FilePayload | None, str, str, str]] = {}
    download_map: dict[str, str] = {}

    def _fetch_identity(asset_path: str) -> str:
        """Coalesce equivalent references before submitting network work."""
        resolved = (
            asset_path
            if asset_path.startswith(("http://", "https://"))
            else urljoin(base_url.rstrip("/") + "/", asset_path)
        )
        try:
            parsed = urlparse(resolved)
            cache_busters = {
                "v", "ver", "version", "cb", "cache", "cachebust",
                "cache_bust", "cachebuster", "t", "ts", "timestamp", "_", "dpl",
            }
            query = [
                (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.lower() not in cache_busters
            ]
            return urlunparse((
                parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
                "", urlencode(query), "",
            ))
        except (TypeError, ValueError):
            return resolved.split("#", 1)[0]

    # ── Website mode: images already downloaded at original paths ─────
    # WebsiteDownloader preserves directory structure. Skip re-downloading
    # relative paths that already exist on disk (prevents rename collisions).
    if site_folder and os.path.isdir(site_folder):
        for asset_paths, sentinel_mime in (
            (image_paths, "image"),
            (audio_paths, "audio/mpeg"),
        ):
            for asset_path in list(asset_paths):
                clean = ""
                if asset_path.startswith(("http://", "https://")):
                    # Absolute same-origin references can already exist in the
                    # website folder under their authored route (for example
                    # loading/point.png). Reuse them instead of creating a
                    # redundant images/loading/ copy.
                    parsed_asset = urlparse(asset_path)
                    parsed_base = urlparse(base_url)
                    base_path = parsed_base.path.rstrip('/')
                    if (
                        parsed_asset.scheme.lower() == parsed_base.scheme.lower()
                        and parsed_asset.netloc.lower() == parsed_base.netloc.lower()
                        and base_path
                        and parsed_asset.path.startswith(base_path + '/')
                    ):
                        clean = _safe_rel_path(
                            parsed_asset.path[len(base_path):].lstrip('/')
                        )
                else:
                    clean = _safe_rel_path(asset_path.lstrip('./').lstrip('/'))
                if not clean:
                    continue
                disk  = _safe_join(site_folder, clean)
                if os.path.exists(disk):
                    logger.debug(f"  [site exists, kept] {clean}")
                    asset_paths.discard(asset_path)
                    fetch_cache[asset_path] = (b"__site_existing__", sentinel_mime, asset_path, "")
                    download_map[asset_path] = clean   # keep original relative path

    # Recompute after removing relative images already present in the ICC
    # folder. The earlier value was calculated before this check, so those
    # paths still entered the executor and were fetched a second time.
    all_downloadable = image_paths | audio_paths

    # Pre-populate with yt-dlp downloads — already on disk, no network fetch needed
    for _yt_rel in _yt_local.values():
        # _yt_rel = "audio/ID.mp3" — marks as successfully "downloaded"
        fetch_cache[_yt_rel] = (b"__yt_local__", "audio/mpeg", _yt_rel, "")
        if download:
            download_map[_yt_rel] = _yt_rel

    # Discord recovery belongs here, after project discovery and before files
    # are localized. It therefore works identically for project.json and for a
    # project object extracted from an embedded JavaScript bundle.
    discord_urls = {path for path in image_paths if is_discord_attachment_url(path)}
    discord_client = None
    discord_refresh_lock = threading.Lock()
    discord_refreshed_urls: dict[str, str] = {}
    transport_headless_lock = threading.Lock()
    transport_headless_events: dict[str, threading.Event] = {}
    transport_headless_failed_domains: set[str] = set()
    # Assets occupying the executor's initial worker slots are a single
    # concurrent probe cohort.  Mark them before submission so a very fast
    # failure in one worker cannot make another initial worker skip its first
    # direct HTTP attempt merely because that thread started a moment later.
    transport_initial_probe_assets: set[str] = set()
    browser_session = None
    if discord_urls and discord_recovery_enabled():
        discord_token = resolve_discord_bot_token()
        if discord_token:
            discord_client = DiscordAttachmentClient(discord_token)
            logger.info(
                "Discord recovery is active for %d attachment(s); direct download is tried first.",
                len(discord_urls),
            )
        else:
            logger.info(
                "Discord attachment URL(s) found. Configure a Bot Token in Settings > "
                "Discord Attachments to recover expired URLs automatically."
            )

    def _refresh_discord_url(original_url: str) -> str:
        if discord_client is None or not is_discord_attachment_url(original_url):
            return ""
        # Multiple image workers can fail together. Serialize this small API
        # call and memoize each original URL so it is refreshed at most once.
        with discord_refresh_lock:
            if original_url in discord_refreshed_urls:
                return discord_refreshed_urls[original_url]
            try:
                refreshed = discord_client.refresh_urls([original_url]).get(original_url, "")
            except (DiscordAttachmentError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Discord URL refresh failed: %s", exc)
                refreshed = ""
            discord_refreshed_urls[original_url] = refreshed
            return refreshed

    def payload_prefix(content):
        return content.prefix if isinstance(content, FilePayload) else content

    def read_response(response):
        if _spool:
            try:
                iterator = getattr(response, "iter_content", None)
                # Compatibility backends may return an already-buffered body.
                chunks = iterator(chunk_size=MEDIA_CHUNK_BYTES) if callable(iterator) else (response.content,)
                return spool_chunks(chunks, _spool,
                                    response=response, on_chunk=_throttle_bandwidth)
            except ResponseBodySizeError as exc:
                raise requests.exceptions.ChunkedEncodingError(str(exc)) from exc
        return response.content

    def retain_payload(url, content):
        if _spool:
            if not isinstance(content, FilePayload):
                content = spool_chunks((content,), _spool)
            _cache_put_file(url, content)
        else:
            _cache_put(url, content)
        return content

    def fetch_gallery(url):
        if _spool and getattr(_fetch_via_gallery_dl, "supports_file_payloads", False):
            return _fetch_via_gallery_dl(url, spool_dir=_spool)
        return _fetch_via_gallery_dl(url)

    def fetch_one(asset_path: str):
        asset_url = (
            asset_path
            if asset_path.startswith(("http://", "https://"))
            else urljoin(base_url.rstrip("/") + "/", asset_path)
        )
        if not vpn_requirement_satisfied():
            return (
                asset_path, None, "", asset_url,
                "VPN guard blocked asset request: required interface not detected",
            )
        # Repair URLs extracted from hand-authored HTML such as https:/cdn/… .
        asset_url = re.sub(
            r"^(https?):/(?!/)", r"\1://", asset_url, flags=re.IGNORECASE
        )

        # SSRF screen: refuse cross-origin internal asset
        # hosts (e.g. a remote project.json that points an image at
        # 127.0.0.1:<port> or 169.254.169.254). Same-origin internal assets
        # (localhost CYOA) still pass; --allow-internal-hosts disables this.
        if _ssrf_block_cross_origin(asset_url, base_url):
            logger.warning(f"  [SSRF blocked] cross-origin internal host: {asset_url}")
            return asset_path, None, "", asset_url, "blocked: cross-origin internal host"

        # ── E: Disk cache check ───────────────────────────────────────
        cached = _cache_get_file(asset_url, _spool) if _spool else _cache_get(asset_url)
        if cached is not None:
            mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
            if _asset_is_error_document(mime, payload_prefix(cached)):
                logger.warning(f"  [CACHE REJECTED] error document: {asset_url}")
                cached = None
        if cached is not None:
            logger.info(f"  [CACHE HIT] {asset_url.split('/')[-1]} ({len(cached)//1024}KB)")
            return asset_path, cached, mime, asset_url, ""

        asset_host = (urlparse(asset_url).hostname or "").lower()
        if (
            asset_path in image_paths
            and asset_host
            and asset_path not in transport_initial_probe_assets
        ):
            with transport_headless_lock:
                transport_domain_failed = (
                    asset_host in transport_headless_failed_domains
                )
            if transport_domain_failed:
                logger.info(
                    f"  [Transport] Skipped after domain recovery failed: {asset_host}"
                )
                return (
                    asset_path,
                    None,
                    "",
                    asset_url,
                    "transport and browser recovery failed for domain",
                )

        # A gallery post URL is an extractor input, not a direct image URL.
        # Trying it four times with requests first wastes up to several
        # minutes on HTML/auth/timeouts and cannot yield the intended media.
        # Give recognized gallery hosts to gallery-dl before the HTTP loop;
        # headless remains the fallback below when extraction fails.
        gdl_site = ""
        gdl_pretried = False
        if asset_path in image_paths:
            gdl_site = gdl_site or _is_gallery_dl_site(asset_url)
            if gdl_site and not gdl_pretried:
                gdl_pretried = True
                logger.info(f"  [gallery-dl] Trying ({gdl_site}): {asset_url}")
                gdl_data = fetch_gallery(asset_url)
                if gdl_data:
                    mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                    return asset_path, retain_payload(asset_url, gdl_data), mime, asset_url, ""

        # ── C: CDN-specific headers ───────────────────────────────────
        # fetch_response owns per-domain throttling and backoff. Sleeping here
        # too charged successful project images twice for the same request.
        headers = get_headers_for_url(asset_url) or {}

        last_err = ""
        permanent_http_failure = False
        transport_exhausted = False
        _cookie_session_tried = False
        authentication_failed = False
        _discord_refresh_tried = False

        asset_url_ext = os.path.splitext(urlparse(asset_url).path)[1].lower()
        page_like_image = (
            asset_path in image_paths
            and asset_url_ext not in _DEEP_SCAN_IMAGE_EXTENSIONS
        )
        direct_attempts = 0 if gdl_pretried else (1 if page_like_image else 4)
        for attempt in range(direct_attempts):
            # Direct file URLs get 3 normal + 1 cookie attempt. Recognized
            # gallery post URLs were already handed to gallery-dl above, and
            # extensionless HTML/page candidates get one request before the
            # browser fallback instead of repeating long connection timeouts.
            r = None
            try:
                # ── B: Cookie session on 3rd attempt for auth-protected content
                if attempt == 2 and authentication_failed and not _cookie_session_tried:
                    _cookie_session_tried = True
                    for _browser in ("chrome", "edge", "firefox"):
                        cs = _make_cookie_session(_browser)
                        if cs:
                            rc = None
                            try:
                                _domain_throttle(asset_url)
                                rc = cs.get(
                                    asset_url, headers=headers, timeout=30,
                                    allow_redirects=False,
                                    stream=bool(_spool),
                                )
                                cookie_content = read_response(rc) if rc.status_code == 200 else None
                                if cookie_content is not None and len(cookie_content) > 64:
                                    mime = rc.headers.get("Content-Type", "").split(";")[0].strip() \
                                           or mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                                    if _asset_is_error_document(mime, payload_prefix(cookie_content)):
                                        logger.warning(f"  [Cookie/{_browser}] rejected error document: {asset_url}")
                                        continue
                                    _domain_record_success(asset_url)
                                    logger.info(f"  [Cookie/{_browser}] {asset_url.split('/')[-1]}")
                                    return asset_path, retain_payload(asset_url, cookie_content), mime, asset_url, ""
                            except _NETWORK_OPERATION_ERRORS as _ignored_exc:
                                logger.debug("Ignored recoverable exception in fetch_one (line 14057): %s", _ignored_exc)
                            finally:
                                if rc is not None:
                                    try:
                                        rc.close()
                                    except _RESPONSE_CLEANUP_ERRORS as close_exc:
                                        logger.debug("Cookie response close failed: %s", close_exc)

                # This loop owns HTTP status retries. Connect/pre-header read
                # failures retain the transport's retry policy and None sentinel.
                with caller_status_retry_scope():
                    r = fetch_response(asset_url, extra_headers=headers, timeout=30, as_bytes=not bool(_spool),
                                       stream=bool(_spool), return_error_response=True)
                if r is None:
                    raise requests.RequestException("fetch_response returned None")

                # ── D: Handle 429 with exponential backoff ────────────
                if r.status_code == 429:
                    last_err = "HTTP 429: rate limited"
                    r.close()  # Release the shared transport slot before backoff.
                    backoff = _domain_record_failure(asset_url, 429)
                    retry_after = retry_after_seconds(r.headers.get("Retry-After"))
                    sleep_s = max(backoff, retry_after, wait_time)
                    logger.warning(f"429 — backoff {sleep_s:.1f}s: {asset_url}")
                    if attempt + 1 < direct_attempts:
                        _cancel_aware_sleep(sleep_s)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}: server error"
                    r.close()
                    backoff = _domain_record_failure(asset_url, r.status_code)
                    sleep_s = max(backoff, retry_after_seconds(r.headers.get("Retry-After")))
                    logger.warning(f"{r.status_code} — backoff {sleep_s:.1f}s: {asset_url}")
                    if attempt + 1 < direct_attempts:
                        _cancel_aware_sleep(sleep_s)
                    continue

                if (
                    r.status_code in REFRESHABLE_HTTP_STATUSES
                    and not _discord_refresh_tried
                    and is_discord_attachment_url(asset_url)
                ):
                    _discord_refresh_tried = True
                    r.close()  # Refresh may itself need a shared HTTP slot.
                    refreshed_url = _refresh_discord_url(asset_url)
                    if refreshed_url and refreshed_url != asset_url:
                        logger.info("  [Discord] Refreshed expired attachment URL")
                        asset_url = refreshed_url
                        headers = get_headers_for_url(asset_url) or {}
                        continue

                if r.status_code in (404, 410):
                    last_err = f"HTTP {r.status_code}: asset not found"
                    permanent_http_failure = True
                    logger.warning(
                        f"Permanent HTTP {r.status_code}; skipping retries and browser fallback: {asset_url}"
                    )
                    break

                # Authentication failures are not transient network errors.
                # Retry immediately until the cookie-session attempt has run,
                # then hand the URL to gallery-dl below. Sleeping 10/20/30
                # seconds for every protected gallery page only stalls batches.
                if r.status_code in (401, 403):
                    authentication_failed = True
                    last_err = f"HTTP {r.status_code}: authentication required"
                    logger.warning(f"{last_err}: {asset_url}")
                    if _cookie_session_tried:
                        break
                    continue

                r.raise_for_status()
                mime = r.headers.get("Content-Type", "").split(";")[0].strip()
                if not mime:
                    mime, _ = mimetypes.guess_type(asset_url)
                    mime = mime or "application/octet-stream"
                content = read_response(r)
                if _asset_is_error_document(mime, payload_prefix(content)):
                    response_url = str(getattr(r, "url", None) or asset_url)
                    original = None
                    if "html" in mime and re.search(r"(?:^|/)File:", unquote(urlparse(response_url).path), re.IGNORECASE):
                        if isinstance(content, FilePayload):
                            from ..project.parse import try_decode_bytes
                            html = try_decode_bytes(pathlib.Path(content.path).read_bytes())
                        else:
                            html = r.text
                        original = mediawiki_image_url(html, response_url)
                    if original and not _ssrf_block_cross_origin(original, base_url):
                        linked = fetch_response(original, extra_headers=get_headers_for_url(original),
                                                timeout=30, as_bytes=not bool(_spool), stream=bool(_spool),
                                                return_error_response=True)
                        try:
                            if linked is not None and linked.status_code == 200:
                                linked_mime = linked.headers.get("Content-Type", "").split(";", 1)[0].strip()
                                linked_content = read_response(linked)
                                if len(linked_content) > 64 and not _asset_is_error_document(linked_mime, payload_prefix(linked_content)):
                                    if not _spool:
                                        _throttle_bandwidth(len(linked_content))
                                    _domain_record_success(asset_url)
                                    logger.info("  [Image page] Resolved original file: %s", original)
                                    return asset_path, retain_payload(asset_url, linked_content), linked_mime or mimetypes.guess_type(original)[0] or "application/octet-stream", original, ""
                        finally:
                            if linked is not None:
                                linked.close()
                    last_err = f"Content-Type mismatch for asset: {mime or 'unknown'}"
                    logger.warning(f"  {last_err}: {asset_url}")
                    break

                if not _spool:
                    _throttle_bandwidth(len(content))
                _domain_record_success(asset_url)
                # ── E: Store in disk cache ────────────────────────────
                return asset_path, retain_payload(asset_url, content), mime, asset_url, ""

            except requests.exceptions.SSLError:
                last_err = "TLS certificate verification failed"
                logger.warning(f"  {last_err}: {asset_url}")
                break
            except requests.exceptions.ConnectionError as e:
                err_s = str(e).lower()
                last_err = f"Connection reset (attempt {attempt+1})" \
                    if "connection reset" in err_s or "econnreset" in err_s else str(e)
                logger.warning(f"  {last_err}: {asset_url}")
                _domain_record_failure(asset_url)
                if attempt + 1 < direct_attempts:
                    _cancel_aware_sleep(min(10 * (attempt + 1), 30))
            except requests.RequestException as e:
                last_err = str(e)
                logger.warning(f"Attempt {attempt + 1} failed for {asset_url}: {e}")
                _domain_record_failure(asset_url)
                # fetch_response already applies its own transport retries.
                # A None result means those retries were exhausted; repeating
                # the same request four more times here multiplies DNS/connect
                # failures into multi-minute stalls before headless fallback.
                if "fetch_response returned None" in last_err:
                    transport_exhausted = True
                    break
                if attempt + 1 < direct_attempts:
                    _cancel_aware_sleep(min(10 * (attempt + 1), 30))
            finally:
                # fetch_response returns a live requests response. Always
                # release it after reading the bytes, including retry/HTTP
                # error branches, so large ICC batches do not exhaust pools.
                if r is not None:
                    try:
                        r.close()
                    except _RESPONSE_CLEANUP_ERRORS as close_exc:
                        logger.debug("Asset response close failed for %s: %s", asset_url, close_exc)

        # ── A: Specialized/browser fallbacks (images only) ─────────────
        if asset_path in image_paths:
            # Gallery pages are purpose-built gallery-dl inputs. Try that before
            # loading a full browser, while retaining headless as a fallback.
            gdl_site = gdl_site or _is_gallery_dl_site(asset_url)
            if gdl_site and not gdl_pretried:
                logger.info(f"  [gallery-dl] Trying ({gdl_site}): {asset_url}")
                gdl_data = fetch_gallery(asset_url)
                if gdl_data:
                    mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                    return asset_path, retain_payload(asset_url, gdl_data), mime, asset_url, ""

            # Selenium/headless remains independently gated.
            headless_data = None
            allow_headless = _SELENIUM_ENABLED and runtime_state._SELENIUM_ENABLED and not permanent_http_failure
            headless_domain = (urlparse(asset_url).hostname or "").lower()
            headless_event = None
            headless_leader = False
            if allow_headless and transport_exhausted and headless_domain:
                with transport_headless_lock:
                    if headless_domain in transport_headless_failed_domains:
                        allow_headless = False
                    else:
                        headless_event = transport_headless_events.get(headless_domain)
                        if headless_event is None:
                            headless_event = threading.Event()
                            transport_headless_events[headless_domain] = headless_event
                            headless_leader = True
                if allow_headless and not headless_leader and headless_event is not None:
                    # One browser probe per transport-failed domain. Followers
                    # wait for that result instead of opening serial browser
                    # timeouts for every dead CDN URL.
                    deadline = time.monotonic() + 75
                    completed = False
                    while time.monotonic() < deadline:
                        _raise_if_cancelled()
                        if headless_event.wait(timeout=.05):
                            completed = True
                            break
                    with transport_headless_lock:
                        allow_headless = (
                            completed
                            and headless_domain not in transport_headless_failed_domains
                        )

            if allow_headless:
                try:
                    logger.info(f"  [Headless] Trying browser fetch: {asset_url}")
                    headless_data = _fetch_headless(asset_url, reject_error_documents=True,
                                                    browser_session=browser_session, spool_dir=_spool)
                finally:
                    if headless_leader and headless_event is not None:
                        with transport_headless_lock:
                            if not headless_data:
                                transport_headless_failed_domains.add(headless_domain)
                            transport_headless_events.pop(headless_domain, None)
                            headless_event.set()
            elif transport_exhausted and headless_domain:
                logger.info(
                    f"  [Headless] Skipped after domain probe failed: {headless_domain}"
                )
            if headless_data:
                mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                logger.info(f"  [Headless] ✓ {asset_url.split('/')[-1]} ({len(headless_data)//1024}KB)")
                return asset_path, retain_payload(asset_url, headless_data), mime, asset_url, ""

        logger.error(f"All retries failed: {asset_url}")
        return asset_path, None, "", asset_url, last_err

    dedup_count = 0
    if all_downloadable:
        browser_session = BrowserFetchSession()
        # Keep the configured parallelism while bounding queued futures. Large
        # projects otherwise allocate one future per asset before work is read.
        safe_workers = max(1, int(max_workers or 1))
        ex = ThreadPoolExecutor(max_workers=safe_workers)
        try:
            fetch_groups: dict[str, list[str]] = {}
            for asset_path in all_downloadable:
                fetch_groups.setdefault(_fetch_identity(asset_path), []).append(asset_path)
            transport_initial_probe_assets.update(
                paths[0] for paths in islice(fetch_groups.values(), safe_workers)
            )
            results = iter_bounded_results(
                ex, lambda paths: fetch_one(paths[0]), fetch_groups.values(),
                max_pending=safe_workers * 2, check_cancelled=_raise_if_cancelled,
            )
            for done, (aliases, result) in enumerate(results, 1):
                path, content, mime, resolved, err = result
                # Dedup is applied at save time, after the final local path is known.
                for alias in aliases:
                    fetch_cache[alias] = (content, mime, resolved, err)
                status = "✓" if content is not None else "✗ FAILED"
                alias_count = len(aliases)
                alias_note = f" (+{alias_count - 1} alias)" if alias_count > 1 else ""
                logger.info(f"  [{done}/{len(fetch_groups)}] {status}{alias_note} {resolved.split('/')[-1]}")
        finally:
            # The run owns spool files and cancellation state. Join active
            # producers before either can be cleaned up or reused by a new run.
            try:
                ex.shutdown(wait=True, cancel_futures=True)
            finally:
                browser_session.close()
    # ── Collect failures ───────────────────────────────────────────
    failed_images: list[dict[str, str]] = [
        {"url": resolved, "path": path, "error": err}
        for path, (content, mime, resolved, err) in fetch_cache.items()
        if content is None and path in image_paths
    ]
    failed_audio: list[dict[str, str]] = [
        {"url": resolved, "path": path, "error": err}
        for path, (content, mime, resolved, err) in fetch_cache.items()
        if content is None and path in audio_paths
    ]

    skipped_youtube = len(youtube_paths)
    ok_count = max(
        0,
        summary_total - len(failed_images) - len(failed_audio) - skipped_youtube,
    )
    logger.info(
        f"Download summary: {ok_count} OK, "
        f"{len(failed_images)} image failure(s), "
        f"{len(failed_audio)} audio failure(s), "
        f"{skipped_youtube} YouTube (skipped) "
        f"out of {summary_total} total."
    )
    if failed_images:
        logger.warning(
            f"{len(failed_images)} image(s) could not be downloaded — "
            f"original URLs kept in JSON. Check failed_images.txt."
        )
        _write_failed_images_log(failed_images, report_dir, source_url=source_url)
    if failed_audio:
        logger.warning(
            f"{len(failed_audio)} audio file(s) could not be downloaded "
            f"and will remain as external URLs."
        )
    if failed_images or failed_audio:
        try:
            write_asset_failure_summary(
                failed_images + failed_audio,
                report_dir,
                source_url=source_url,
                title="Broken Project Asset Report",
            )
        except (OSError, TypeError, ValueError) as e:
            logger.debug(f"Broken asset report could not be written: {e}")

    # ── Build replacement maps ─────────────────────────────────────
    embed_map: dict[str, str | EmbeddedMedia] = {}
    saved_payloads: dict[str, str] = {}
    # download_map already initialized above (with yt_local pre-populated)

    for path, (content, mime, resolved, err) in fetch_cache.items():
        _raise_if_cancelled()
        is_image = path in image_paths

        if content is None:
            continue

        # Skip yt-dlp sentinel entries — already in download_map, no re-saving needed
        if content == b"__yt_local__":
            continue

        # Skip site_existing sentinel — file already at original path, path kept
        if content == b"__site_existing__":
            continue

        # ── Successful download ────────────────────────────────────
        if embed:
            if is_image:
                if isinstance(content, FilePayload):
                    embed_map[path] = EmbeddedMedia(content.path, mime)
                else:
                    embed_map[path] = f"data:{mime};base64,{base64.b64encode(content).decode()}"
            elif embed_audio and not is_image:
                # Embed audio as base64 only if file is small enough to inline
                _audio_size_limit = 10 * 1024 * 1024  # 10 MB
                if len(content) <= _audio_size_limit:
                    if isinstance(content, FilePayload):
                        embed_map[path] = EmbeddedMedia(content.path, mime)
                    else:
                        embed_map[path] = f"data:{mime};base64,{base64.b64encode(content).decode()}"
                    logger.info(f"  Embedded audio: {path.split('/')[-1]} ({len(content)//1024} KB)")
                else:
                    logger.warning(
                        f"  Audio too large to embed ({len(content)//1024//1024} MB): "
                        f"{path.split('/')[-1]} — keeping as file reference"
                    )

        if download:
            ext = _media_content_extension(resolved, mime, payload_prefix(content)) or ".bin"

            # ── Preserve relative path structure (directory hierarchy) ─────
            # e.g. ./CYOAs/Images/BranchingHeart/0/3.avif
            #   → images/CYOAs/Images/BranchingHeart/0/3.avif
            # This prevents double-downloads: deep scan checks the same
            # directory structure and will find the file already on disk.
            resolved_parsed = urlparse(resolved)
            base_parsed = urlparse(base_url)
            url_path = resolved_parsed.path.lstrip('/')
            base_url_path = base_parsed.path.rstrip('/')
            # Only strip the base path on a whole-segment
            # match. Plain startswith() also matched partial segments, e.g.
            # base "/tale" + asset "/tale-assets/x.png" → "-assets/x.png",
            # corrupting the local layout and breaking offline references.
            _bp = base_url_path.lstrip('/')
            if _bp and (url_path == _bp or url_path.startswith(_bp + '/')):
                url_path = url_path[len(_bp):]
            url_path = url_path.lstrip('./ ')

            # Cross-origin CDNs commonly expose very deep storage paths such
            # as ``original/05/8b/...``.  Keeping that provider-specific path
            # makes an ICC package unnecessarily huge and can make two
            # providers look like unrelated local assets.  Use one stable,
            # flat filename for external images; the URL hash prevents a
            # basename collision while the host keeps the result readable.
            is_external_image = (
                is_image
                and (
                    resolved_parsed.scheme.lower() != base_parsed.scheme.lower()
                    or resolved_parsed.netloc.lower() != base_parsed.netloc.lower()
                )
            )
            if is_external_image:
                from .package import clean_url_path_component
                host = re.sub(r"[^a-z0-9]+", "_", (resolved_parsed.hostname or "external").lower()).strip("_")
                raw_name = os.path.basename(unquote(resolved_parsed.path)) or "image"
                safe_name = clean_url_path_component(raw_name)
                stem, source_ext = os.path.splitext(safe_name)
                digest = hashlib.sha1(
                    resolved.encode("utf-8", "replace"), usedforsecurity=False
                ).hexdigest()[:12]
                # Keep the final Windows filename bounded too: the host + hash
                # prefix can push an already-long CDN basename over MAX_PATH.
                fn = clean_url_path_component(
                    f"{host or 'external'}_{stem or 'image'}_{digest}{source_ext or ext}"
                )
            elif '/' in url_path:
                # Multi-segment path: preserve directory structure
                fn = url_path
                # Strip leading directory that duplicates dest_folder name
                # e.g. "images/hero.png" saved under images_folder → "images/images/hero.png" (BAD)
                #   → strip to "hero.png" → "images/hero.png" (GOOD)
                _IMAGE_DIR_PREFIXES = ('images/', 'img/', 'image/', 'pics/', 'pictures/', 'assets/images/', 'assets/img/')
                _AUDIO_DIR_PREFIXES = ('audio/', 'music/', 'sounds/', 'sfx/', 'bgm/', 'assets/audio/', 'assets/music/')
                fn_lower = fn.lower()
                if is_image:
                    for pfx in _IMAGE_DIR_PREFIXES:
                        if fn_lower.startswith(pfx):
                            fn = fn[len(pfx):]
                            break
                else:
                    for pfx in _AUDIO_DIR_PREFIXES:
                        if fn_lower.startswith(pfx):
                            fn = fn[len(pfx):]
                            break
            else:
                fn = os.path.basename(url_path) or ("image" if is_image else "audio")

            if not fn:
                fn = "image" if is_image else "audio"
            if not os.path.splitext(fn)[1]:
                fn += ext

            # Audio goes into audio/ subfolder, images into images/
            if is_image:
                dest_folder = images_folder
            else:
                dest_folder = audio_folder

            # Build full destination path (may include subdirectories), guarded against path traversal
            dest_path = _safe_join(dest_folder, fn, fallback=("image" if is_image else "audio") + ext)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            # Collision avoidance: check if file already exists at this path
            base_fn_full, ext_fn = os.path.splitext(dest_path)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = f"{base_fn_full}_{counter}{ext_fn}"
                counter += 1

            duplicate_of = (
                (_check_image_dedup_digest if isinstance(content, FilePayload) else _check_image_dedup)(
                    content.sha256 if isinstance(content, FilePayload) else content,
                    dest_path,
                    scope=os.path.abspath(images_folder),
                )
                if is_image and not site_folder else None
            )
            if duplicate_of and os.path.exists(duplicate_of):
                try:
                    rel_saved = os.path.relpath(duplicate_of, os.path.dirname(images_folder)).replace('\\', '/')
                    download_map[path] = rel_saved
                    dedup_count += 1
                    logger.debug(f"  [DEDUP] {path.split('/')[-1]} -> {rel_saved}")
                    continue
                except (OSError, TypeError, ValueError) as _ignored_exc:
                    logger.debug("Ignored recoverable exception in process_images (line 14307): %s", _ignored_exc)

            if isinstance(content, FilePayload):
                saved = saved_payloads.get(content.path)
                if saved:
                    atomic_copy_file(saved, dest_path)
                else:
                    # Spools live beneath temp_folder on the output volume.
                    # The first owner can commit by rename; aliases retain its
                    # destination as their file source if a second copy is needed.
                    try:
                        os.replace(content.path, dest_path)
                    except OSError as exc:
                        if exc.errno != errno.EXDEV:
                            raise
                        atomic_copy_file(content.path, dest_path)
                    saved_payloads[content.path] = dest_path
            else:
                atomic_write_bytes(dest_path, content)

            # Relative path from site root: "images/CYOAs/Images/.../3.avif"
            rel_saved = os.path.relpath(dest_path, os.path.dirname(images_folder)).replace('\\', '/')
            download_map[path] = rel_saved

    if dedup_count:
        logger.info(f"  [DEDUP] {dedup_count} duplicate image(s) reused instead of saved again")

    # Both mode may have committed a spool by rename while building disk paths.
    # Point embedded descriptors at that final file before consuming any chunks.
    for path, value in embed_map.items():
        if isinstance(value, EmbeddedMedia):
            embed_map[path] = EmbeddedMedia(saved_payloads.get(value.path, value.path), value.mime)

    dl_str = ("".join(project_chunks(input_str, download_map, field_group,
                                     image_fields_lower, image_field_aliases))
              if download else input_str)
    embed_str = input_str
    if embed:
        chunks = project_chunks(input_str, embed_map, field_group,
                                image_fields_lower, image_field_aliases)
        if embed_writer is not None:
            embed_writer(chunks)
            embed_str = ""
        else:
            embed_str = "".join(chunks)

    # Collect successfully downloaded/resolved URLs for skip-list
    _resolved_urls: set[str] = set()
    for path, (content, mime, resolved, err) in fetch_cache.items():
        if content is not None and resolved:
            _resolved_urls.add(resolved)
            # Also add the original asset path resolved to absolute
            if not path.startswith(("http://", "https://")):
                _resolved_urls.add(urljoin(base_url.rstrip("/") + "/", path))

    return embed_str, dl_str, _resolved_urls


def _deep_scan_and_download_assets(
    folder: str,
    base_url: str,
    output_dir: str,
    wait_time: int = DEFAULT_WAIT_TIME,
    max_workers: int = DEFAULT_MAX_WORKERS,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "aggressive_recovery",
    ai_budget: AIUsageBudget | None = None,
    skip_urls: set[str] | None = None,
    exclude_relative_paths: set[str] | None = None,
    source_urls: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Iteratively scan ALL JS, CSS, and HTML files in `folder` for asset
    URL references, download missing ones, then re-scan newly downloaded
    files — repeating until no new assets are found (BFS convergence).

    Handles both project.json-based CYOAs and pure custom React/Vite viewers.
    ``source_urls`` maps absolute local paths to remote document bases and is
    extended with the effective response URLs of newly saved files.
    """
    from concurrent.futures import ThreadPoolExecutor as _TPE

    TEXT_EXTS   = {'.js', '.css', '.html', '.htm', '.mjs', '.cjs', '.json', '.hjson', '.svg'}
    all_downloaded: dict[str, str] = {}   # url → rel_path
    failed_deep_assets: list[dict[str, str]] = []
    scanned_files: set[str]        = set()   # abs file paths already scanned
    known_urls:    set[str]        = set()   # canonical candidate URLs already seen
    candidate_sources: dict[str, set[str]] = {}
    generated_images = _GeneratedEntryImageIndex()
    css_sources: set[str] = set()
    file_sources = source_urls if source_urls is not None else {}
    response_sources: dict[str, str] = {}
    response_sources_lock = threading.Lock()
    excluded_paths = {
        str(path).replace('\\', '/').lstrip('./').lower()
        for path in (exclude_relative_paths or set())
    }

    @lru_cache(maxsize=8192)
    def _canonical_scan_url(url: str) -> str:
        """Canonicalize scan candidates without collapsing real variants."""
        try:
            parsed = urlparse(str(url).strip())
            if parsed.scheme.lower() not in {"http", "https"}:
                return str(url)
            cache_busters = {
                "v", "ver", "version", "cb", "cache", "cachebust",
                "cache_bust", "cachebuster", "t", "ts", "timestamp", "_", "dpl",
            }
            query = [
                (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.lower() not in cache_busters
            ]
            return urlunparse((
                parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
                "", urlencode(query), "",
            ))
        except (TypeError, ValueError):
            return str(url).split("#", 1)[0]

    # Pre-populate with URLs already downloaded by process_images
    # (prevents double-downloading the same assets)
    if skip_urls:
        known_urls |= {_canonical_scan_url(url) for url in skip_urls}
        logger.debug(f"[deep scan] Skipping {len(skip_urls)} URL(s) already downloaded by process_images")

    # Refresh this index during the candidate walk, avoiding a second complete
    # traversal while still observing files added/removed between rounds.
    _disk_files: set[str] = set()

    def _collect_candidates_from_folder(scan_folder: str) -> set[str]:
        """Scan all unscanned text files in scan_folder, return new candidate URLs."""
        new_candidates: set[str] = set()
        _disk_files.clear()
        for root, directories, files in os.walk(scan_folder):
            _raise_if_cancelled()
            directories[:] = [name for name in directories if name != "__original_site__"]
            for fn in files:
                _raise_if_cancelled()
                fpath = os.path.join(root, fn)
                rel = os.path.relpath(fpath, folder).replace('\\', '/')
                _disk_files.add(rel)
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                if fpath in scanned_files:
                    continue
                scanned_files.add(fpath)
                if rel.lower() in excluded_paths:
                    continue
                f_url = file_sources.get(os.path.abspath(fpath)) or urljoin(
                    base_url.rstrip('/') + '/', quote(rel, safe="/"),
                )
                if ext == '.css':
                    css_sources.add(f_url)
                try:
                    with open(fpath, encoding='utf-8', errors='replace') as _fh:
                        text = _fh.read()
                    generated_images.add_source(text)
                    urls = run_asset_scanner_plugins(text, f_url, base_url, ext)
                    canonical_urls = {_canonical_scan_url(url) for url in urls}
                    new_candidates |= (canonical_urls - known_urls)
                    for candidate in canonical_urls:
                        candidate_sources.setdefault(candidate, set()).add(f_url)
                except _ASSET_SCANNER_PLUGIN_ERRORS as e:
                    logger.debug(f"[deep scan] {fn}: {e}")
        _raise_if_cancelled()
        for inferred_path in generated_images.paths():
            candidate = _canonical_scan_url(
                urljoin(base_url.rstrip('/') + '/', inferred_path)
            )
            candidate_sources.setdefault(candidate, set()).add(
                "generated Entry.id image contract"
            )
            if candidate not in known_urls:
                new_candidates.add(candidate)
        return new_candidates

    def _record_response_source(url: str, response) -> None:
        effective = str(getattr(response, "url", "") or url)
        try:
            parsed = urlparse(effective)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                effective = url
        except ValueError:
            effective = url
        with response_sources_lock:
            response_sources[url] = effective

    def _remember_download(url: str, rel: str, local: str) -> None:
        all_downloaded[url] = rel
        _disk_files.add(rel)
        with response_sources_lock:
            source = response_sources.get(url, url)
        file_sources[os.path.abspath(local)] = source

    def _url_to_local(url: str, content: bytes | None = None) -> str:
        """Convert an asset URL to a safe relative path within the folder."""
        parsed   = urlparse(url)
        base_parsed = urlparse(base_url)

        # Preserve the authored directory layout only for the site being
        # archived. External CDN paths are storage details (and often contain
        # giant hash trees such as Tumblr/Wix/Pinterest paths), so keep those
        # assets flat and grouped by type instead.
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.netloc.lower() != base_parsed.netloc.lower()
        ):
            return _deep_scan_external_rel_path(url, content)

        rel_path = parsed.path.lstrip('/')
        base_path = urlparse(base_url).path.rstrip('/')
        # Whole-segment match only; plain startswith()
        # mangled sibling paths like "/game" vs "/gamedata/app.js".
        _bp = base_path.lstrip('/')
        if _bp and (rel_path == _bp or rel_path.startswith(_bp + '/')):
            rel_path = rel_path[len(_bp):]
        rel_path = rel_path.lstrip('/')
        if parsed.query:
            root, ext = os.path.splitext(rel_path)
            digest = hashlib.sha1(
                parsed.query.encode("utf-8", "replace"), usedforsecurity=False
            ).hexdigest()[:10]
            rel_path = f"{root}_{digest}{ext}"
        return _safe_rel_path(rel_path)

    failed_keys: set[tuple[str, str]] = set()

    def _record_deep_failure(
        url: str,
        rel_path: str | None,
        error: str,
    ) -> None:
        rel = rel_path or _url_to_local(url)
        key = (url, error)
        if key in failed_keys:
            return
        failed_keys.add(key)
        failed_deep_assets.append({
            "url": url,
            "path": rel,
            "error": error,
            "kind": "deep-scan",
        })
        _emit_progress_event(
            "file_failed",
            name=os.path.basename(rel or urlparse(url).path) or url,
            url=url,
            error=error,
        )

    def _deep_scan_rel_for_content(url: str, content: bytes) -> str | None:
        """Validate a deep-scan response before choosing its local path.

        Deep scan candidates are often extracted from JSON fields that are
        labelled as images, but a remote URL can still return a branded HTML
        page with HTTP 200 (for example an image proxy landing page).  Keep
        this check immediately before every deep-scan write as a last line of
        defence, and derive the extension from the actual bytes so extension-
        less CDN images do not fall into ``external/assets/*.bin``.
        """
        if _asset_is_error_document("", content):
            rel_hint = _url_to_local(url)
            _record_deep_failure(
                url,
                rel_hint,
                "response was an HTML/error document, not a binary asset",
            )
            logger.warning(f"  [deep rejected] HTML/error document: {url}")
            return None
        return _url_to_local(url, content) or os.path.basename(urlparse(url).path) or "asset"

    def _css_root_fallback(url: str) -> str | None:
        """Return a page-root alternative for a failed CSS asset."""
        refs = candidate_sources.get(_canonical_scan_url(url), set())
        if not any(ref in css_sources or urlparse(ref).path.lower().endswith(".css") for ref in refs):
            return None
        parsed = urlparse(url)
        base = urlparse(base_url)
        if (
            parsed.scheme.lower() != base.scheme.lower()
            or parsed.netloc.lower() != base.netloc.lower()
        ):
            return None
        base_path = base.path.rstrip("/")
        candidate_path = parsed.path
        if not base_path or not candidate_path.startswith(base_path + "/"):
            return None
        relative = candidate_path[len(base_path) + 1:]
        filename = relative.rsplit("/", 1)[-1]
        if "/" not in relative or not filename:
            return None
        alt_path = base_path + "/" + filename
        if alt_path == candidate_path:
            return None
        return urlunparse(parsed._replace(path=alt_path))

    def _viewer_route_asset_fallback(url: str) -> str | None:
        """Prefix a deployed viewer route onto a failed root /assets URL."""
        parsed = urlparse(url)
        base = urlparse(base_url)
        route_prefix = base.path.rstrip("/")
        if not route_prefix or not parsed.path.startswith("/assets/"):
            return None
        if (
            parsed.scheme.lower() != base.scheme.lower()
            or parsed.netloc.lower() != base.netloc.lower()
            or parsed.path.startswith(route_prefix + "/")
        ):
            return None
        return urlunparse(parsed._replace(path=route_prefix + parsed.path))

    _http2_client = None
    if runtime_state._HTTP2_ENABLED:
        try:
            import httpx  # type: ignore
            _proxy_mode_current = str(runtime_state._proxy_mode)
            # Advanced manual proxy routing (per-scheme endpoints and
            # NO_PROXY) is enforced by the shared requests pipeline. A single
            # httpx.Client proxy cannot preserve that full profile, so keep
            # correctness and fall back instead of bypassing/misrouting it.
            if _proxy_mode_current == "manual":
                logger.info(
                    "[deep scan] HTTP/2 deferred: manual advanced proxy profile "
                    "uses the shared requests pipeline"
                )
            else:
                _http2_kwargs = {
                    "http2": True,
                    "follow_redirects": False,
                    "timeout": 20,
                    "headers": {"User-Agent": "Mozilla/5.0"},
                    "trust_env": _proxy_mode_current == "inherit_env",
                }
                _http2_client = httpx.Client(**_http2_kwargs)
                logger.info("[deep scan] HTTP/2 enabled via httpx")
        except _OPTIONAL_HTTP2_ERRORS as e:
            logger.warning(f"[deep scan] HTTP/2 unavailable, falling back to requests: {e}")
            _http2_client = None

    def _try_fetch(url: str) -> tuple[str, bytes | None, int]:
        """Single-pass GET with unified fallback support.

        Tries HTTP/2 first when enabled, then falls back to fetch_response so
        Cloudflare/FlareSolverr, proxy, DNS, and retry policy remain consistent.
        """
        _raise_if_cancelled()
        # Recovery copies are evidence, even if live markup references them.
        # Apply this at the shared fetch boundary, including fallback/AI jobs.
        if "__original_site__" in _url_to_local(url).split("/"):
            return url, None, 200
        if not vpn_requirement_satisfied():
            logger.error(
                "[deep scan] VPN guard blocked request; required interface not detected"
            )
            return url, None, 0
        # SSRF screen: a scanned JS/CSS/HTML/project file can
        # reference a cross-origin internal host. Block it unless same-origin as
        # the page (base_url) or --allow-internal-hosts is set.
        if _ssrf_block_cross_origin(url, base_url):
            logger.warning(f"  [SSRF blocked] cross-origin internal host: {url}")
            return url, None, 0
        r2 = None
        r = None
        try:
            hdrs = get_headers_for_url(url) or {}
            if _http2_client is not None:
                try:
                    _domain_throttle(url)
                    with HTTP_BUDGET.acquire(url):
                        r2 = _http2_client.get(url, headers=hdrs)
                    if r2.status_code == 200:
                        content = r2.content
                        mime = r2.headers.get("Content-Type", "")
                        if _asset_is_error_document(mime, content, binary_asset=False):
                            return url, None, 200
                        _record_response_source(url, r2)
                        _throttle_bandwidth(len(content))
                        return url, content, 200
                    if r2.status_code not in {301, 302, 303, 307, 308, 403, 429, 503}:
                        return url, None, r2.status_code
                except _OPTIONAL_HTTP2_ERRORS as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _try_fetch (line 19184): %s", _ignored_exc)
            r = fetch_response(url, extra_headers=hdrs, timeout=20, as_bytes=True)
            if r is not None and r.status_code == 200:
                content = r.content
                if _asset_is_error_document(
                    r.headers.get("Content-Type", ""), content, binary_asset=False
                ):
                    return url, None, 200
                _record_response_source(url, r)
                _throttle_bandwidth(len(content))
                return url, content, 200
            return url, None, int(getattr(r, "status_code", 0) or 0)
        except DownloadCancelledError:
            raise
        except _NETWORK_OPERATION_ERRORS:
            return url, None, 0
        finally:
            for response in (r2, r):
                if response is not None:
                    try:
                        response.close()
                    except _RESPONSE_CLEANUP_ERRORS as close_exc:
                        logger.debug("Deep-scan response close failed for %s: %s", url, close_exc)

    def _fetch_many(urls: list[str]):
        """Fetch a batch without waiting for cancelled futures to drain."""
        ex = _TPE(max_workers=_dl_workers)
        try:
            for _url, result in iter_bounded_results(
                ex, _try_fetch, urls, max_pending=_dl_workers * 2,
                check_cancelled=_raise_if_cancelled,
            ):
                yield result
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    round_n = 0
    max_rounds = 6   # safety cap

    # Manifests are downloaded only when a local text asset explicitly
    # references them; speculative root probes caused slow 404/retry loops.
    # ── Parallel concurrency: scale with workers, cap at 20 ───────────
    # Respect the GUI's worker setting. The old minimum of eight spawned a
    # surprisingly large pool even when the user selected one worker, which
    # made low-spec laptops contend with the GUI and disk scanner.
    _dl_workers = max(1, min(int(max_workers or 1), 8))

    # Guarantee HTTP/2 client + pooled session are closed
    # even if the BFS loop raises. Previously close() ran only on the happy
    # path, leaking the httpx connection pool (and _session was never closed).
    try:
        while round_n < max_rounds:
            round_n += 1

            # ── Step 1: collect candidate URLs from all unscanned files ──
            new_candidates = _collect_candidates_from_folder(folder)
            if not new_candidates:
                logger.debug(f"[deep scan] Round {round_n}: no new candidates → done")
                break

            known_urls |= new_candidates
            logger.debug(f"[deep scan] Round {round_n}: {len(new_candidates)} new candidate(s)")

            # ── Step 2: filter out already-on-disk files (O(1) set check) ─
            to_download: list[str] = []
            for url in new_candidates:
                rel = _url_to_local(url)
                if rel and rel in _disk_files:
                    continue   # already on disk
                to_download.append(url)

            if not to_download:
                logger.debug(f"[deep scan] Round {round_n}: all already on disk")
                break

            logger.info(f"[deep scan] Round {round_n}: fetching {len(to_download)} asset(s)…")

            # ── Step 3: single-pass parallel GET (replaces HEAD+GET) ──────
            new_this_round = 0
            vite_retry: list[str] = []
            vite_origins: dict[str, tuple[str, int]] = {}

            for url, content, status in _fetch_many(to_download):
                    _raise_if_cancelled()
                    if content:
                        rel = _deep_scan_rel_for_content(url, content)
                        if rel is None:
                            continue
                        local = _safe_archive_join(folder, rel, decode_url=False)
                        os.makedirs(os.path.dirname(local), exist_ok=True)
                        try:
                            atomic_write_bytes(local, content)
                            _remember_download(url, rel, local)
                            new_this_round += 1
                            logger.info(f"  [deep ✓] {rel}")
                        except _FILE_OPERATION_ERRORS as e:
                            _record_deep_failure(url, rel, f"save failed: {e}")
                            logger.debug(f"[deep scan] save {rel}: {e}")
                    elif status != 200:
                        fallback_url = _css_root_fallback(url)
                        if fallback_url:
                            fallback_rel = (
                                _url_to_local(fallback_url)
                                or os.path.basename(urlparse(fallback_url).path)
                                or "asset"
                            )
                            if fallback_url in all_downloaded or fallback_rel in _disk_files:
                                continue
                            known_urls.add(fallback_url)
                            _, fallback_content, _ = _try_fetch(fallback_url)
                            if fallback_content:
                                fallback_rel = (
                                    _url_to_local(fallback_url, fallback_content)
                                    or os.path.basename(urlparse(fallback_url).path)
                                    or "asset"
                                )
                                fallback_local = _safe_archive_join(folder, fallback_rel, decode_url=False)
                                os.makedirs(os.path.dirname(fallback_local), exist_ok=True)
                                try:
                                    atomic_write_bytes(fallback_local, fallback_content)
                                    _remember_download(fallback_url, fallback_rel, fallback_local)
                                    new_this_round += 1
                                    logger.info(f"  [deep ✓ CSS root] {fallback_rel}")
                                    continue
                                except _FILE_OPERATION_ERRORS as e:
                                    logger.debug(f"[deep scan] CSS root fallback save {fallback_rel}: {e}")
                        route_url = _viewer_route_asset_fallback(url)
                        if route_url:
                            route_rel = (
                                _url_to_local(route_url)
                                or os.path.basename(urlparse(route_url).path)
                                or "asset"
                            )
                            known_urls.add(route_url)
                            _, route_content, _ = _try_fetch(route_url)
                            if route_content:
                                route_rel = (
                                    _url_to_local(route_url, route_content)
                                    or os.path.basename(urlparse(route_url).path)
                                    or "asset"
                                )
                                route_local = _safe_archive_join(folder, route_rel, decode_url=False)
                                os.makedirs(os.path.dirname(route_local), exist_ok=True)
                                try:
                                    atomic_write_bytes(route_local, route_content)
                                    _remember_download(route_url, route_rel, route_local)
                                    new_this_round += 1
                                    logger.info(f"  [deep ✓ route] {route_rel}")
                                    continue
                                except _FILE_OPERATION_ERRORS as e:
                                    logger.debug(f"[deep scan] route fallback save {route_rel}: {e}")
                        # Vite correction: if root-level JS 404'd, try /assets/
                        p = urlparse(url).path
                        if p.count('/') == 1 and p.lower().endswith(('.js', '.mjs')):
                            parsed = urlparse(url)
                            alt = urlunparse(parsed._replace(path='/assets' + parsed.path))
                            alt = _canonical_scan_url(alt)
                            if alt not in known_urls:
                                vite_retry.append(alt)
                                vite_origins[alt] = (url, status)
                                known_urls.add(alt)
                                continue
                        _record_deep_failure(
                            url,
                            _url_to_local(url),
                            f"HTTP {status or 'request failed'}",
                        )

            # ── Vite /assets/ retry for root-level 404s ───────────────────
            if vite_retry:
                for url, content, status in _fetch_many(vite_retry):
                        _raise_if_cancelled()
                        if content:
                            rel = _deep_scan_rel_for_content(url, content)
                            if rel is None:
                                continue
                            local = _safe_archive_join(folder, rel, decode_url=False)
                            os.makedirs(os.path.dirname(local), exist_ok=True)
                            try:
                                atomic_write_bytes(local, content)
                                _remember_download(url, rel, local)
                                new_this_round += 1
                                logger.info(f"  [deep ✓ Vite] {rel}")
                            except _FILE_OPERATION_ERRORS as e:
                                _record_deep_failure(url, rel, f"save failed: {e}")
                                logger.debug(f"[deep scan] save {rel}: {e}")
                        elif status != 200:
                            original_url, original_status = vite_origins.get(
                                url, (url, status)
                            )
                            _record_deep_failure(
                                original_url,
                                _url_to_local(original_url),
                                f"HTTP {original_status or status or 'request failed'}",
                            )
                logger.debug(f"[deep scan] Vite /assets/ correction: "
                             f"{len(vite_retry)} tried")

            logger.info(f"[deep scan] Round {round_n}: {new_this_round} saved"
                        + (" — rescanning for new refs…" if new_this_round else ""))

            if new_this_round == 0:
                break   # nothing new downloaded → converged

    finally:
        if _http2_client is not None:
            try:
                _http2_client.close()
            except _OPTIONAL_HTTP2_ERRORS as _ignored_exc:
                logger.debug("Ignored close exc (_http2_client) in deep scan: %s", _ignored_exc)
    if all_downloaded:
        logger.info(f"[deep scan] Complete: {len(all_downloaded)} total asset(s) downloaded"
                    f" in {round_n} round(s)")

    # ── AI-assisted final round ────────────────────────────────────────
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode)
    if _ai_mode_allows("asset_scan", ai_mode) and _ai_is_available(ai_api_key, ai_provider):
        try:
            js_files_ai: dict[str, str] = {}
            for _root, _, _files in os.walk(folder):
                for _fn in _files:
                    if _fn.endswith(('.js', '.mjs', '.cjs')):
                        _fp = os.path.join(_root, _fn)
                        try:
                            _ct = pathlib.Path(_fp).read_text(encoding='utf-8', errors='replace')
                            if len(_ct) > 500:
                                js_files_ai[os.path.relpath(_fp, folder)] = _ct
                        except (OSError, TypeError, ValueError) as _ignored_exc:
                            logger.debug("Ignored recoverable exception in _deep_scan_and_download_assets (line 19341): %s", _ignored_exc)

            if js_files_ai:
                ai_candidates = _ai_analyze_js_for_assets(js_files_ai, base_url, api_key=ai_api_key,
                    provider=ai_provider, ai_mode=ai_mode, budget_obj=ai_budget)
                ai_new = []
                for candidate in ai_candidates:
                    canonical = _canonical_scan_url(candidate)
                    if canonical not in known_urls and canonical not in all_downloaded:
                        ai_new.append(canonical)
                if ai_new:
                    logger.info(f"[AI scan] {len(ai_new)} new candidate(s) from AI analysis")
                    known_urls.update(ai_new)

                    ai_new_count = 0
                    def _try_fetch_ai(url: str) -> tuple[str, bytes | None, int]:
                        return _try_fetch(url)

                    for url_ai, content_ai, _ in _fetch_many(ai_new):
                            _raise_if_cancelled()
                            if not content_ai:
                                continue
                            rel_ai = _deep_scan_rel_for_content(url_ai, content_ai)
                            if rel_ai is None:
                                continue
                            local_ai = _safe_archive_join(folder, rel_ai, decode_url=False)
                            os.makedirs(os.path.dirname(local_ai), exist_ok=True)
                            try:
                                atomic_write_bytes(local_ai, content_ai)
                                _remember_download(url_ai, rel_ai, local_ai)
                                ai_new_count += 1
                                logger.info(f"  [AI ✓] {rel_ai}")
                            except _FILE_OPERATION_ERRORS as _ignored_exc:
                                logger.debug("Ignored recoverable exception in _deep_scan_and_download_assets (line 19372): %s", _ignored_exc)
                    logger.info(f"[AI scan] Done — {ai_new_count} new asset(s)")
        except DownloadCancelledError:
            raise
        except _OPTIONAL_AI_ERRORS as e:
            logger.debug(f"[AI scan] error: {e}")

    if failed_deep_assets:
        try:
            report_target = write_asset_failure_summary(
                failed_deep_assets,
                folder,
                source_url=base_url,
                title="Broken Deep-Scan Asset Report",
            )
            logger.warning(f"[deep scan] {len(failed_deep_assets)} asset(s) failed; see {os.path.basename(report_target or 'backup_report.txt')}")
        except (OSError, TypeError, ValueError) as e:
            logger.debug(f"[deep scan] broken report failed: {e}")

    return all_downloaded


__all__ = [
    "_check_image_dedup",
    "_deep_scan_and_download_assets",
    "_deep_scan_project_assets",
    "_download_youtube_audio",
    "_find_ffmpeg",
    "_is_probable_raw_cdn_asset",
    "_make_ytdlp_hook",
    "_patch_youtube_refs_in_json",
    "_safe_response_text",
    "_scan_file_for_assets",
    "_write_failed_images_log",
    "_write_youtube_skip_log",
    "process_images",
]
