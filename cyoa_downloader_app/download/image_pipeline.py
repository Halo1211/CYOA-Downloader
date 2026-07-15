"""Image/audio/deep-scan pipeline domain implementation.

Phase 39 moves the two large image/deep-scan orchestration functions out of
legacy.py while preserving their historical globals through a compatibility
namespace snapshot. The function bodies are intentionally mechanical copies;
output names, report formats, and download behavior are unchanged.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

# The legacy module still owns several mutable GUI/network flags during the
# transition. Import it once and copy its already-initialized public surface so
# the moved legacy function bodies can resolve historical global names.
from ._bridge import legacy as _legacy
from ..app_info import DEFAULT_MAX_WORKERS, DEFAULT_WAIT_TIME

from .asset_scan import (
    _is_probable_raw_cdn_asset,
    _check_image_dedup,
    _make_placeholder_svg,
    _PLACEHOLDER_DATA_URI,
    _safe_response_text,
    _scan_file_for_assets,
    _deep_scan_project_assets,
)
from .audio_reports import (
    _write_failed_images_log,
    _write_youtube_skip_log,
    _find_ffmpeg,
    _patch_youtube_refs_in_json,
)
from .audio_download import _make_ytdlp_hook, _download_youtube_audio
from .headers import get_headers_for_url
from ..core.cancellation import _cancel_requested, _raise_if_cancelled
from ..core.progress import DownloadCancelledError


def _asset_is_error_document(mime: str, content: bytes) -> bool:
    """Reject successful HTTP error pages masquerading as image/audio data."""
    content_type = (mime or "").split(";", 1)[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml", "application/json"}:
        return True
    prefix = bytes(content or b"")[:512].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def process_images(
    input_str: str,
    base_url: str,
    embed: bool = False,
    download: bool = False,
    temp_folder: Optional[str] = None,
    wait_time: int = DEFAULT_WAIT_TIME,
    max_workers: int = DEFAULT_MAX_WORKERS,
    output_dir: str = "",
    source_url: str = "",
    embed_audio: bool = False,
    site_folder: str = "",   # ICC mode: check if images already exist here
) -> Tuple[str, str, Set[str]]:
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
    deep_images, deep_audio, deep_youtube = _deep_scan_project_assets(input_str, base_url)

    # ── Phase B: Regex scan as fallback/supplement ──────────────────
    # Catches cases where JSON parsing fails (truncated payload, embedded JS, etc.)
    all_fields   = IMAGE_FIELDS + AUDIO_FIELDS
    field_group  = "|".join(re.escape(f) for f in all_fields)
    pattern      = rf'"({field_group})"\s*:\s*"([^"]+)"'
    image_fields_lower = {f.lower() for f in IMAGE_FIELDS}

    image_paths:   Set[str] = set(deep_images)
    audio_paths:   Set[str] = set(deep_audio)
    youtube_paths: Set[str] = set(deep_youtube)

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
            image_paths.add(path)
        else:
            audio_paths.add(path)

    # Remove data URIs and blanks that slipped through
    for s in (image_paths, audio_paths, youtube_paths):
        s.discard("")
        to_remove = {p for p in s if data_uri_re.match(p)}
        s -= to_remove

    if not image_paths and not audio_paths and not youtube_paths:
        logger.info("No external images or audio found.")
        return input_str, input_str, set()

    # Log summary
    logger.info(
        f"Assets found: {len(image_paths)} image(s), "
        f"{len(audio_paths)} direct audio file(s), "
        f"{len(youtube_paths)} YouTube reference(s) (kept as-is)."
    )
    _yt_local: Dict[str, str] = {}   # yt-dlp downloaded files: local_rel → local_rel

    if youtube_paths:
        yt_audio_dir = temp_folder if temp_folder else output_dir
        yt_map = _download_youtube_audio(
            sorted(youtube_paths), yt_audio_dir, source_url=source_url,
            log_dir=output_dir,
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
    fetch_cache: Dict[str, Tuple[Optional[bytes], str, str, str]] = {}
    download_map: Dict[str, str] = {}

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
        except Exception:
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

    def fetch_one(asset_path: str):
        asset_url = (
            asset_path
            if asset_path.startswith(("http://", "https://"))
            else urljoin(base_url.rstrip("/") + "/", asset_path)
        )

        # SSRF screen: refuse cross-origin internal asset
        # hosts (e.g. a remote project.json that points an image at
        # 127.0.0.1:<port> or 169.254.169.254). Same-origin internal assets
        # (localhost CYOA) still pass; --allow-internal-hosts disables this.
        if _ssrf_block_cross_origin(asset_url, base_url):
            logger.warning(f"  [SSRF blocked] cross-origin internal host: {asset_url}")
            return asset_path, None, "", asset_url, "blocked: cross-origin internal host"

        # ── E: Disk cache check ───────────────────────────────────────
        cached = _cache_get(asset_url)
        if cached is not None:
            mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
            if _asset_is_error_document(mime, cached):
                logger.warning(f"  [CACHE REJECTED] error document: {asset_url}")
                cached = None
        if cached is not None:
            logger.info(f"  [CACHE HIT] {asset_url.split('/')[-1]} ({len(cached)//1024}KB)")
            return asset_path, cached, mime, asset_url, ""

        # ── C: CDN-specific headers ───────────────────────────────────
        _domain_throttle(asset_url)  # D: also checks domain backoff
        headers = get_headers_for_url(asset_url) or {}

        last_err = ""
        permanent_http_failure = False
        _cookie_session_tried = False

        for attempt in range(4):  # 4 attempts: 3 normal + 1 cookie
            r = None
            try:
                # ── B: Cookie session on 3rd attempt for auth-protected content
                if attempt == 2 and not _cookie_session_tried:
                    _cookie_session_tried = True
                    for _browser in ("chrome", "edge", "firefox"):
                        cs = _make_cookie_session(_browser)
                        if cs:
                            rc = None
                            try:
                                rc = cs.get(
                                    asset_url, headers=headers, timeout=30,
                                    allow_redirects=False,
                                )
                                if rc.status_code == 200 and len(rc.content) > 64:
                                    mime = rc.headers.get("Content-Type", "").split(";")[0].strip() \
                                           or mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                                    if _asset_is_error_document(mime, rc.content):
                                        logger.warning(f"  [Cookie/{_browser}] rejected error document: {asset_url}")
                                        continue
                                    _domain_record_success(asset_url)
                                    _cache_put(asset_url, rc.content)
                                    logger.info(f"  [Cookie/{_browser}] {asset_url.split('/')[-1]}")
                                    return asset_path, rc.content, mime, asset_url, ""
                            except Exception as _ignored_exc:
                                logger.debug("Ignored recoverable exception in fetch_one (line 14057): %s", _ignored_exc)
                            finally:
                                if rc is not None:
                                    try:
                                        rc.close()
                                    except Exception:
                                        pass

                r = fetch_response(asset_url, extra_headers=headers, timeout=30, as_bytes=True, return_error_response=True)
                if r is None:
                    raise requests.RequestException("fetch_response returned None")

                # ── D: Handle 429 with exponential backoff ────────────
                if r.status_code == 429:
                    backoff = _domain_record_failure(asset_url, 429)
                    retry_after_raw = r.headers.get("Retry-After", "")
                    try:
                        retry_after = int(float(retry_after_raw)) if retry_after_raw else int(backoff)
                    except Exception:
                        retry_after = int(backoff)
                    sleep_s = max(backoff, retry_after, wait_time)
                    logger.warning(f"429 — backoff {sleep_s:.1f}s: {asset_url}")
                    _cancel_aware_sleep(sleep_s)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    backoff = _domain_record_failure(asset_url, r.status_code)
                    logger.warning(f"{r.status_code} — backoff {backoff:.1f}s: {asset_url}")
                    _cancel_aware_sleep(backoff)
                    continue

                if r.status_code in (404, 410):
                    last_err = f"HTTP {r.status_code}: asset not found"
                    permanent_http_failure = True
                    logger.warning(
                        f"Permanent HTTP {r.status_code}; skipping retries and browser fallback: {asset_url}"
                    )
                    break

                r.raise_for_status()
                mime = r.headers.get("Content-Type", "").split(";")[0].strip()
                if not mime:
                    mime, _ = mimetypes.guess_type(asset_url)
                    mime = mime or "application/octet-stream"
                if _asset_is_error_document(mime, r.content):
                    last_err = f"Content-Type mismatch for asset: {mime or 'unknown'}"
                    logger.warning(f"  {last_err}: {asset_url}")
                    break

                _throttle_bandwidth(len(r.content))
                _domain_record_success(asset_url)
                # ── E: Store in disk cache ────────────────────────────
                _cache_put(asset_url, r.content)
                return asset_path, r.content, mime, asset_url, ""

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
                if attempt < 3: _cancel_aware_sleep(min(10 * (attempt + 1), 30))
            except requests.RequestException as e:
                last_err = str(e)
                logger.warning(f"Attempt {attempt + 1} failed for {asset_url}: {e}")
                _domain_record_failure(asset_url)
                if attempt < 3: _cancel_aware_sleep(min(10 * (attempt + 1), 30))
            finally:
                # fetch_response returns a live requests response. Always
                # release it after reading the bytes, including retry/HTTP
                # error branches, so large ICC batches do not exhaust pools.
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass

        # ── A: Headless fallback (images only) ───────────────────────
        if asset_path in image_paths:
            # Selenium/headless gated independently so gallery-dl (below) still runs
            headless_data = None
            if _SELENIUM_ENABLED and not permanent_http_failure:
                logger.info(f"  [Headless] Trying browser fetch: {asset_url}")
                headless_data = _fetch_headless(asset_url, reject_error_documents=True)
            if headless_data:
                mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                _cache_put(asset_url, headless_data)
                logger.info(f"  [Headless] ✓ {asset_url.split('/')[-1]} ({len(headless_data)//1024}KB)")
                return asset_path, headless_data, mime, asset_url, ""

            # ── F: gallery-dl (Pixiv, booru, DeviantArt auth) ────────
            gdl_site = _is_gallery_dl_site(asset_url)
            if gdl_site:
                logger.info(f"  [gallery-dl] Trying ({gdl_site}): {asset_url}")
                gdl_data = _fetch_via_gallery_dl(asset_url)
                if gdl_data:
                    mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                    _cache_put(asset_url, gdl_data)
                    return asset_path, gdl_data, mime, asset_url, ""

        logger.error(f"All retries failed: {asset_url}")
        return asset_path, None, "", asset_url, last_err

    dedup_count = 0
    if all_downloadable:
        # Two hardening fixes for the executor block:

        # (1) Clamp max_workers. process_images is a module-level public function
        #     callable outside run_download (CLI, tests, future callers) where the
        #     value is not pre-clamped. ThreadPoolExecutor raises ValueError on
        #     max_workers <= 0, so an unclamped 0/negative would crash here rather
        #     than degrade. Floor at 1.

        # (2) Cancel-aware collection. as_completed() previously drained every
        #     future even after the user cancelled mid-batch — fetch_one's sleeps
        #     are cancel-aware, but the collector kept pulling results, so a large
        #     batch felt unresponsive on cancel. Check _cancel_requested() each
        #     iteration and break, then shut the pool down without waiting and
        #     cancel not-yet-started futures (cancel_futures, 3.9+).
        safe_workers = max(1, int(max_workers or 1))
        ex = ThreadPoolExecutor(max_workers=safe_workers)
        try:
            fetch_groups: Dict[str, List[str]] = {}
            for asset_path in all_downloadable:
                fetch_groups.setdefault(_fetch_identity(asset_path), []).append(asset_path)
            futures = {
                ex.submit(fetch_one, paths[0]): paths
                for paths in fetch_groups.values()
            }
            done = 0
            cancelled = False
            for fut in as_completed(futures):
                if _cancel_requested():
                    cancelled = True
                    break
                path, content, mime, resolved, err = fut.result()
                # Dedup is applied at save time, after the final local path is known.
                for alias in futures[fut]:
                    fetch_cache[alias] = (content, mime, resolved, err)
                done += 1
                status = "✓" if content is not None else "✗ FAILED"
                alias_count = len(futures[fut])
                alias_note = f" (+{alias_count - 1} alias)" if alias_count > 1 else ""
                logger.info(f"  [{done}/{len(fetch_groups)}] {status}{alias_note} {resolved.split('/')[-1]}")
            if cancelled:
                ex.shutdown(wait=False, cancel_futures=True)
                raise DownloadCancelledError("Download cancelled during asset fetch")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    # ── Collect failures ───────────────────────────────────────────
    failed_images: List[Dict[str, str]] = [
        {"url": resolved, "path": path, "error": err}
        for path, (content, mime, resolved, err) in fetch_cache.items()
        if content is None and path in image_paths
    ]
    failed_audio: List[Dict[str, str]] = [
        {"url": resolved, "path": path, "error": err}
        for path, (content, mime, resolved, err) in fetch_cache.items()
        if content is None and path in audio_paths
    ]

    ok_count = sum(1 for _, (c, _, _, _) in fetch_cache.items() if c is not None)
    logger.info(
        f"Download summary: {ok_count} OK, "
        f"{len(failed_images)} image failure(s), "
        f"{len(failed_audio)} audio failure(s), "
        f"{len(youtube_paths)} YouTube (skipped) "
        f"out of {len(all_downloadable) + len(youtube_paths)} total."
    )
    if failed_images:
        logger.warning(
            f"{len(failed_images)} image(s) could not be downloaded — "
            f"original URLs kept in JSON. Check failed_images.txt."
        )
        _write_failed_images_log(failed_images, output_dir, source_url=source_url)
    if failed_audio:
        logger.warning(
            f"{len(failed_audio)} audio file(s) could not be downloaded "
            f"and will remain as external URLs."
        )
    if failed_images or failed_audio:
        try:
            write_asset_failure_summary(
                failed_images + failed_audio,
                output_dir or os.getcwd(),
                source_url=source_url,
                title="Broken Project Asset Report",
            )
        except Exception as e:
            logger.debug(f"Broken asset report could not be written: {e}")

    # ── Build replacement maps ─────────────────────────────────────
    embed_map:  Dict[str, str] = {}
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
                b64 = base64.b64encode(content).decode()
                embed_map[path] = f"data:{mime};base64,{b64}"
            elif embed_audio and not is_image:
                # Embed audio as base64 only if file is small enough to inline
                _audio_size_limit = 10 * 1024 * 1024  # 10 MB
                if len(content) <= _audio_size_limit:
                    b64 = base64.b64encode(content).decode()
                    embed_map[path] = f"data:{mime};base64,{b64}"
                    logger.info(f"  Embedded audio: {path.split('/')[-1]} ({len(content)//1024} KB)")
                else:
                    logger.warning(
                        f"  Audio too large to embed ({len(content)//1024//1024} MB): "
                        f"{path.split('/')[-1]} — keeping as file reference"
                    )

        if download:
            ext = mimetypes.guess_extension(mime) or ".bin"
            if ext in (".jpe", ".jpeg"):
                ext = ".jpg"

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
                host = re.sub(r"[^a-z0-9]+", "_", (resolved_parsed.hostname or "external").lower()).strip("_")
                raw_name = os.path.basename(unquote(resolved_parsed.path)) or "image"
                stem, source_ext = os.path.splitext(raw_name)
                digest = hashlib.sha1(resolved.encode("utf-8", "replace")).hexdigest()[:12]
                fn = f"{host or 'external'}_{stem or 'image'}_{digest}{source_ext}"
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
                rel_prefix  = "images"
            else:
                dest_folder = audio_folder
                rel_prefix  = "audio"

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
                _check_image_dedup(
                    content,
                    dest_path,
                    scope=os.path.abspath(images_folder),
                )
                if is_image else None
            )
            if duplicate_of and os.path.exists(duplicate_of):
                try:
                    rel_saved = os.path.relpath(duplicate_of, os.path.dirname(images_folder)).replace('\\', '/')
                    download_map[path] = rel_saved
                    dedup_count += 1
                    logger.debug(f"  [DEDUP] {path.split('/')[-1]} -> {rel_saved}")
                    continue
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in process_images (line 14307): %s", _ignored_exc)

            atomic_write_bytes(dest_path, content)

            # Relative path from site root: "images/CYOAs/Images/.../3.avif"
            rel_saved = os.path.relpath(dest_path, os.path.dirname(images_folder)).replace('\\', '/')
            download_map[path] = rel_saved

    if dedup_count:
        logger.info(f"  [DEDUP] {dedup_count} duplicate image(s) reused instead of saved again")

    # ── Single-pass substitution ───────────────────────────────────
    # The regex `pattern` covers IMAGE_FIELDS + AUDIO_FIELDS field names.
    # But bgmId (when useAudioURL=true) is found only by the deep scanner —
    # we add it to the pattern here so it gets rewritten too.
    # bgmId direct-audio values (when useAudioURL=true) are discovered by the
    # deep scanner and land in embed_map/download_map like any other audio path.
    # They are rewritten by the secondary string-replace pass below (not by the
    # field-name regex), so no separate bgmId pattern is needed here.

    def make_embed(m: re.Match) -> str:
        field, path = m.group(1), m.group(2)
        if data_uri_re.match(path):
            return m.group(0)
        # Map keys are canonical (unescaped); normalize the
        # text-form path so escaped occurrences rewrite too.
        key = path.replace("\\/", "/") if "\\/" in path else path
        return f'"{field}":"{embed_map.get(key, path)}"'

    def make_download(m: re.Match) -> str:
        field, path = m.group(1), m.group(2)
        if data_uri_re.match(path):
            return m.group(0)
        key = path.replace("\\/", "/") if "\\/" in path else path
        return f'"{field}":"{download_map.get(key, path)}"'

    # Single-pass replacement. The previous implementation
    # applied sequential str.replace() per map entry. If one entry's replacement
    # value happened to equal another entry's original key (e.g. a localized
    # output path colliding with a different asset's original reference — possible
    # since download_map can hold local-relative keys), the second pass would
    # re-rewrite the already-substituted text (chained double-rewrite → wrong
    # path). A single regex pass that consults the map per match cannot chain,
    # because each region of the string is visited exactly once. Output is
    # identical to the old code for the normal (non-colliding) case.
    def _single_pass_asset_sub(text: str, mapping: Dict[str, str]) -> str:
        if not mapping:
            return text
        # Replace the remote token wherever it appears, including inside
        # escaped JSON strings such as `<img src=\"https://...\">`. Longest
        # keys first prevent a shorter URL from shadowing a longer one.
        keys = sorted(mapping.keys(), key=len, reverse=True)
        alt = "|".join(re.escape(k) for k in keys)
        asset_re = re.compile(alt)

        def _repl(m: "re.Match") -> str:
            return mapping.get(m.group(0), m.group(0))

        return asset_re.sub(_repl, text)

    # Apply deep-scan mappings to the original text first, then run the field
    # mapper. Doing this in the opposite order would let a source key such as
    # ``same.png`` match inside its newly-created replacement
    # ``images/same.png`` and produce ``images/images/same.png``.
    embed_source = _single_pass_asset_sub(input_str, embed_map) if embed else input_str
    download_source = _single_pass_asset_sub(input_str, download_map) if download else input_str

    # Primary substitution using field-name regex
    embed_str = re.sub(pattern, make_embed, embed_source, flags=re.IGNORECASE) if embed else input_str
    dl_str    = re.sub(pattern, make_download, download_source, flags=re.IGNORECASE) if download else input_str

    # Collect successfully downloaded/resolved URLs for skip-list
    _resolved_urls: Set[str] = set()
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
    ai_budget: Optional[AIUsageBudget] = None,
    skip_urls: Optional[Set[str]] = None,
    exclude_relative_paths: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """
    Iteratively scan ALL JS, CSS, and HTML files in `folder` for asset
    URL references, download missing ones, then re-scan newly downloaded
    files — repeating until no new assets are found (BFS convergence).

    Handles both project.json-based CYOAs and pure custom React/Vite viewers.
    """
    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed

    TEXT_EXTS   = {'.js', '.css', '.html', '.htm', '.mjs', '.cjs', '.json', '.svg'}
    all_downloaded: Dict[str, str] = {}   # url → rel_path
    failed_deep_assets: List[Dict[str, str]] = []
    scanned_files: Set[str]        = set()   # abs file paths already scanned
    known_urls:    Set[str]        = set()   # canonical candidate URLs already seen
    candidate_sources: Dict[str, Set[str]] = {}
    excluded_paths = {
        str(path).replace('\\', '/').lstrip('./').lower()
        for path in (exclude_relative_paths or set())
    }

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
        except Exception:
            return str(url).split("#", 1)[0]

    # Pre-populate with URLs already downloaded by process_images
    # (prevents double-downloading the same assets)
    if skip_urls:
        known_urls |= {_canonical_scan_url(url) for url in skip_urls}
        logger.debug(f"[deep scan] Skipping {len(skip_urls)} URL(s) already downloaded by process_images")

    # ── Pre-build disk file index for O(1) existence checks ───────────
    # Walking the folder once is far cheaper than calling os.path.exists()
    # for every candidate URL individually.
    _disk_files: Set[str] = set()

    def _rebuild_disk_index() -> None:
        _disk_files.clear()
        for _root, _, _fnames in os.walk(folder):
            for _fn in _fnames:
                _rel = os.path.relpath(os.path.join(_root, _fn), folder)
                _disk_files.add(_rel.replace('\\', '/'))

    _rebuild_disk_index()

    def _collect_candidates_from_folder(scan_folder: str) -> Set[str]:
        """Scan all unscanned text files in scan_folder, return new candidate URLs."""
        new_candidates: Set[str] = set()
        for root, _, files in os.walk(scan_folder):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                fpath = os.path.join(root, fn)
                if fpath in scanned_files:
                    continue
                scanned_files.add(fpath)
                rel    = os.path.relpath(fpath, folder).replace('\\', '/')
                if rel.lower() in excluded_paths:
                    continue
                f_url  = urljoin(base_url.rstrip('/') + '/', rel)
                try:
                    with open(fpath, encoding='utf-8', errors='replace') as _fh:
                        text = _fh.read()
                    urls = run_asset_scanner_plugins(text, f_url, base_url, ext)
                    canonical_urls = {_canonical_scan_url(url) for url in urls}
                    new_candidates |= (canonical_urls - known_urls)
                    for candidate in canonical_urls:
                        candidate_sources.setdefault(candidate, set()).add(f_url)
                except Exception as e:
                    logger.debug(f"[deep scan] {fn}: {e}")
        return new_candidates

    def _url_to_local(url: str) -> str:
        """Convert absolute URL to relative path within the folder."""
        parsed   = urlparse(url)
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
            digest = hashlib.sha1(parsed.query.encode("utf-8", "replace")).hexdigest()[:10]
            rel_path = f"{root}_{digest}{ext}"
        return rel_path

    def _css_root_fallback(url: str) -> Optional[str]:
        """Return a page-root alternative for a failed CSS asset."""
        refs = candidate_sources.get(_canonical_scan_url(url), set())
        if not any(urlparse(ref).path.lower().endswith(".css") for ref in refs):
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

    # ── Reusable session with connection pooling ──────────────────────
    # Keeps TCP connections alive across requests to the same host,
    # avoiding repeated TLS handshakes (saves ~100-200ms per request).
    _session = requests.Session()
    _session.headers.update({"User-Agent": "Mozilla/5.0"})
    _adapter = requests.adapters.HTTPAdapter(
        pool_connections=16, pool_maxsize=32, max_retries=1)
    _session.mount("https://", _adapter)
    _session.mount("http://", _adapter)
    _proxy = _get_active_proxy()
    if _proxy:
        _session.proxies.update({"http": _proxy, "https": _proxy})

    _http2_client = None
    if getattr(_legacy(), "_HTTP2_ENABLED", False):
        try:
            import httpx  # type: ignore
            _http2_kwargs = dict(
                http2=True,
                follow_redirects=False,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if _proxy:
                # httpx changed proxy keyword behavior across versions. Try modern
                # proxy= first, then fall back to proxies= for older releases.
                try:
                    _http2_client = httpx.Client(proxy=_proxy, **_http2_kwargs)
                except TypeError:
                    _http2_client = httpx.Client(proxies={"http://": _proxy, "https://": _proxy}, **_http2_kwargs)
                logger.info("[deep scan] HTTP/2 enabled via httpx with proxy")
            else:
                _http2_client = httpx.Client(**_http2_kwargs)
                logger.info("[deep scan] HTTP/2 enabled via httpx")
        except Exception as e:
            logger.warning(f"[deep scan] HTTP/2 unavailable, falling back to requests: {e}")
            _http2_client = None

    def _try_fetch(url: str) -> Tuple[str, Optional[bytes], int]:
        """Single-pass GET with unified fallback support.

        Tries HTTP/2 first when enabled, then falls back to fetch_response so
        Cloudflare/FlareSolverr, proxy, DNS, and retry policy remain consistent.
        """
        _raise_if_cancelled()
        # SSRF screen: a scanned JS/CSS/HTML/project file can
        # reference a cross-origin internal host. Block it unless same-origin as
        # the page (base_url) or --allow-internal-hosts is set.
        if _ssrf_block_cross_origin(url, base_url):
            logger.warning(f"  [SSRF blocked] cross-origin internal host: {url}")
            return url, None, 0
        r2 = None
        r = None
        try:
            _domain_throttle(url)
            hdrs = get_headers_for_url(url) or {}
            if _http2_client is not None:
                try:
                    r2 = _http2_client.get(url, headers=hdrs)
                    if r2.status_code == 200:
                        content = r2.content
                        mime = r2.headers.get("Content-Type", "")
                        if _asset_is_error_document(mime, content):
                            return url, None, 200
                        _throttle_bandwidth(len(content))
                        return url, content, 200
                    if r2.status_code not in {301, 302, 303, 307, 308, 403, 429, 503}:
                        return url, None, r2.status_code
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _try_fetch (line 19184): %s", _ignored_exc)
            r = fetch_response(url, extra_headers=hdrs, timeout=20, as_bytes=True)
            if r is not None and r.status_code == 200:
                content = r.content
                _throttle_bandwidth(len(content))
                return url, content, 200
            return url, None, int(getattr(r, "status_code", 0) or 0)
        except DownloadCancelledError:
            raise
        except Exception:
            return url, None, 0
        finally:
            for response in (r2, r):
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

    def _fetch_many(urls: List[str]):
        """Fetch a batch without waiting for cancelled futures to drain."""
        ex = _TPE(max_workers=_dl_workers)
        futures = [ex.submit(_try_fetch, url) for url in urls]
        try:
            for future in as_completed(futures):
                _raise_if_cancelled()
                yield future.result()
        except BaseException:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        else:
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
            to_download: List[str] = []
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
            vite_retry: List[str] = []

            for url, content, status in _fetch_many(to_download):
                    _raise_if_cancelled()
                    if content:
                        rel = _url_to_local(url)
                        if not rel:
                            rel = os.path.basename(urlparse(url).path) or 'asset'
                        local = _safe_join(folder, rel)
                        os.makedirs(os.path.dirname(local), exist_ok=True)
                        try:
                            atomic_write_bytes(local, content)
                            all_downloaded[url] = rel
                            _disk_files.add(rel)
                            new_this_round += 1
                            logger.info(f"  [deep ✓] {rel}")
                        except Exception as e:
                            failed_deep_assets.append({"url": url, "path": rel, "error": f"save failed: {e}", "kind": "deep-scan"})
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
                                fallback_local = _safe_join(folder, fallback_rel)
                                os.makedirs(os.path.dirname(fallback_local), exist_ok=True)
                                try:
                                    atomic_write_bytes(fallback_local, fallback_content)
                                    all_downloaded[fallback_url] = fallback_rel
                                    _disk_files.add(fallback_rel)
                                    new_this_round += 1
                                    logger.info(f"  [deep ✓ CSS root] {fallback_rel}")
                                    continue
                                except Exception as e:
                                    logger.debug(f"[deep scan] CSS root fallback save {fallback_rel}: {e}")
                        failed_deep_assets.append({"url": url, "path": _url_to_local(url), "error": f"HTTP {status or 'request failed'}", "kind": "deep-scan"})
                        # Vite correction: if root-level JS 404'd, try /assets/
                        p = urlparse(url).path
                        if p.count('/') == 1 and p.lower().endswith(('.js', '.mjs')):
                            parsed = urlparse(url)
                            alt = urlunparse(parsed._replace(path='/assets' + parsed.path))
                            alt = _canonical_scan_url(alt)
                            if alt not in known_urls:
                                vite_retry.append(alt)
                                known_urls.add(alt)

            # ── Vite /assets/ retry for root-level 404s ───────────────────
            if vite_retry:
                for url, content, status in _fetch_many(vite_retry):
                        _raise_if_cancelled()
                        if content:
                            rel = _url_to_local(url)
                            if not rel:
                                rel = os.path.basename(urlparse(url).path) or 'asset'
                            local = _safe_join(folder, rel)
                            os.makedirs(os.path.dirname(local), exist_ok=True)
                            try:
                                atomic_write_bytes(local, content)
                                all_downloaded[url] = rel
                                _disk_files.add(rel)
                                new_this_round += 1
                                logger.info(f"  [deep ✓ Vite] {rel}")
                            except Exception as e:
                                failed_deep_assets.append({"url": url, "path": rel, "error": f"save failed: {e}", "kind": "deep-scan"})
                                logger.debug(f"[deep scan] save {rel}: {e}")
                logger.debug(f"[deep scan] Vite /assets/ correction: "
                             f"{len(vite_retry)} tried")

            logger.info(f"[deep scan] Round {round_n}: {new_this_round} saved"
                        + (" — rescanning for new refs…" if new_this_round else ""))

            if new_this_round == 0:
                break   # nothing new downloaded → converged

            # Refresh disk index after downloads
            _rebuild_disk_index()

    finally:
        if _http2_client is not None:
            try:
                _http2_client.close()
            except Exception as _ignored_exc:
                logger.debug("Ignored close exc (_http2_client) in deep scan: %s", _ignored_exc)
        try:
            _session.close()
        except Exception as _ignored_exc:
            logger.debug("Ignored close exc (_session) in deep scan: %s", _ignored_exc)

    if all_downloaded:
        logger.info(f"[deep scan] Complete: {len(all_downloaded)} total asset(s) downloaded"
                    f" in {round_n} round(s)")

    # ── AI-assisted final round ────────────────────────────────────────
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode)
    if _ai_mode_allows("asset_scan", ai_mode) and _ai_is_available(ai_api_key, ai_provider):
        try:
            js_files_ai: Dict[str, str] = {}
            for _root, _, _files in os.walk(folder):
                for _fn in _files:
                    if _fn.endswith(('.js', '.mjs', '.cjs')):
                        _fp = os.path.join(_root, _fn)
                        try:
                            _ct = pathlib.Path(_fp).read_text(encoding='utf-8', errors='replace')
                            if len(_ct) > 500:
                                js_files_ai[os.path.relpath(_fp, folder)] = _ct
                        except Exception as _ignored_exc:
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
                    def _try_fetch_ai(url: str) -> Tuple[str, Optional[bytes], int]:
                        return _try_fetch(url)

                    for url_ai, content_ai, _ in _fetch_many(ai_new):
                            _raise_if_cancelled()
                            if not content_ai:
                                continue
                            rel_ai = _url_to_local(url_ai)
                            if not rel_ai:
                                rel_ai = os.path.basename(urlparse(url_ai).path) or 'asset'
                            local_ai = _safe_join(folder, rel_ai)
                            os.makedirs(os.path.dirname(local_ai), exist_ok=True)
                            try:
                                atomic_write_bytes(local_ai, content_ai)
                                all_downloaded[url_ai] = rel_ai
                                ai_new_count += 1
                                logger.info(f"  [AI ✓] {rel_ai}")
                            except Exception as _ignored_exc:
                                logger.debug("Ignored recoverable exception in _deep_scan_and_download_assets (line 19372): %s", _ignored_exc)
                    logger.info(f"[AI scan] Done — {ai_new_count} new asset(s)")
        except DownloadCancelledError:
            raise
        except Exception as e:
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
        except Exception as e:
            logger.debug(f"[deep scan] broken report failed: {e}")

    return all_downloaded


# Snapshot compatibility globals only after all functions exist. This permits
# direct module import while runtime.surface is still composing its facade and
# ensures late-defined helpers such as get_headers_for_url are included.
_l = _legacy()
for _name, _value in vars(_l).items():
    globals().setdefault(_name, _value)


__all__ = [
    "_is_probable_raw_cdn_asset", "_check_image_dedup",
    "_make_placeholder_svg", "_PLACEHOLDER_DATA_URI",
    "_safe_response_text", "_scan_file_for_assets",
    "_deep_scan_project_assets", "_write_failed_images_log",
    "_write_youtube_skip_log", "_find_ffmpeg", "_make_ytdlp_hook",
    "_download_youtube_audio", "_patch_youtube_refs_in_json",
    "process_images", "_deep_scan_and_download_assets",
]
