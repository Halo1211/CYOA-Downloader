"""Website mirroring domain implementation.

Phase 37 moved ``WebsiteDownloader`` out of ``legacy.py``. The class body is
kept mechanically equivalent; high-risk collaborators still resolve through
small runtime proxies so global legacy state and patch ordering remain intact.
"""

from __future__ import annotations

import os
import pathlib
import re
import threading
import hashlib
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

import requests

from ._bridge import legacy
from .asset_scan import _infer_dynamic_asset_paths, _safe_response_text
from .headers import get_headers_for_url
from .package import (
    atomic_stream_response_to_file,
    clean_url_path_component,
    get_first_subdomain,
)
from ..constants.assets import (
    AUDIO_EXTENSIONS,
    FONT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SCRIPT_EXTENSIONS,
    STYLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    _YOUTUBE_URL_RE,
)
from ..core.atomic_io import atomic_write_text
from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..core.paths import _safe_join
from ..core.url_utils import _directory_base_url
from ..diagnostics.reports import format_backup_report_text
from ..integrations.ai_core import (
    AIUsageBudget,
    _get_ai_provider,
    _normalize_ai_mode,
    _normalize_ai_provider,
    _ssrf_block_cross_origin,
)
from ..config.settings import _load_settings
from ..logging_setup import logger
from ..project.discover import get_source, url_file_exists, get_first_folder_from_url, strip_document_from_url
from ..project.parse import is_zip_bytes

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - mirrors legacy fallback behavior
    def BeautifulSoup(*_args, **_kwargs):  # type: ignore
        raise RuntimeError(
            "Missing dependency: beautifulsoup4 is required for HTML/ICC parsing. "
            "Install it with: pip install beautifulsoup4"
        )


def create_retry_session(*args, **kwargs):
    return legacy().create_retry_session(*args, **kwargs)


def fetch_response(*args, **kwargs):
    return legacy().fetch_response(*args, **kwargs)


def _deep_scan_and_download_assets(*args, **kwargs):
    return legacy()._deep_scan_and_download_assets(*args, **kwargs)


def _throttle_bandwidth(*args, **kwargs):
    return legacy()._throttle_bandwidth(*args, **kwargs)


def _auto_profile_uses_project_data(value) -> bool:
    """Return True when Auto already has an authoritative project payload."""
    if isinstance(value, dict):
        return str(value.get("detected_engine") or "").lower() == "project_json"
    return str(getattr(value, "detected_engine", "") or "").lower() == "project_json"

class WebsiteDownloader:
    """
    Download a viewer into a clean offline package like:
      index.html
      project.json
      css/
      js/
      images/
      fonts/
      assets/

    Differences from v3:
      • output is flattened to viewer-style root layout
      • HTML/CSS/JS are analysed, not just project.json
      • external fonts/scripts/styles from index.html and CSS are localized too
      • JS string URLs are scanned (similar to Extract_Link.py + test.py workflow)
    """

    _quoted_asset_re = re.compile(
        r'(?P<quote>["\'])(?P<url>(?:https?:)?//[^"\']+|(?:\./|\.\./|/)?[^"\']+\.(?:json|txt|zip|js|mjs|css|png|jpe?g|gif|webp|avif|bmp|svg|ico|mp3|ogg|wav|m4a|aac|opus|woff2?|ttf|otf|eot)(?:\?[^"\']*)?)(?P=quote)',
        re.IGNORECASE,
    )
    _css_url_re = re.compile(r'url\(([^)]+)\)', re.IGNORECASE)
    _css_import_re = re.compile(
        r'@import\s+(?:url\()?["\']?([^"\')\s]+)["\']?\)?',
        re.IGNORECASE,
    )
    _telemetry_hosts = {
        "www.googletagmanager.com", "googletagmanager.com",
        "www.google-analytics.com", "google-analytics.com",
        "stats.g.doubleclick.net", "cct.google", "vercel.live",
        "www.clarity.ms", "clarity.ms", "browser.sentry-cdn.com",
    }

    def __init__(self, start_url: str, output_folder: str, max_workers: int = 4,
                 ai_api_key: str = "", ai_provider: str = "",
                 ai_mode: str = "auto_fallback",
                 ai_budget: Optional[AIUsageBudget] = None,
                 archive_strategy: str = "classic") -> None:
        self.start_url     = start_url
        self.output_folder = output_folder
        self.max_workers   = max_workers
        self.ai_api_key    = ai_api_key
        self.ai_provider   = _normalize_ai_provider(ai_provider or _get_ai_provider())
        self.ai_mode       = _normalize_ai_mode(ai_mode or _load_settings().get("ai_mode", "auto_fallback"))
        self.ai_budget     = ai_budget
        self.archive_strategy = str(archive_strategy or "classic").strip().lower()
        # base_url = directory portion of start_url (used for resolving
        # relative paths).  Extensionless routes such as ``/drukhari`` are
        # viewer directories, not documents; the shared helper preserves
        # that final route segment.
        self.base_url = _directory_base_url(start_url)
        self.max_workers = max_workers
        self.session = create_retry_session()
        self._lock = threading.Lock()
        self._downloaded: Dict[str, str] = {}
        self._source_for_local: Dict[str, str] = {}
        self._used_local_paths: Set[str] = set()
        parsed = urlparse(start_url)
        self.base_origin = f"{parsed.scheme}://{parsed.netloc}"
        self.start_html_local = os.path.join(self.output_folder, "index.html")
        self._success_items: List[Dict[str, str]] = []
        self._failed_items: List[Dict[str, str]] = []
        self._project_aliases: List[str] = []
        self._collision_log: List[Dict[str, str]] = []
        self._custom_viewer_route = False

    def download(self) -> None:
        os.makedirs(self.output_folder, exist_ok=True)
        logger.info(f"ICC download started: {self.start_url}")
        self._download_html(self.start_url, self.start_html_local)
        logger.info(f"ICC package saved: {self.output_folder}/")
        # Deep scan: find assets referenced in JS/CSS bundles not in HTML
        if (
            self.archive_strategy == "auto"
            and _auto_profile_uses_project_data(
                getattr(self, "archive_auto_profile", None)
            )
        ):
            logger.info(
                "[Auto] Project data is authoritative; skipping redundant "
                "pre-project JS/CSS deep scan."
            )
        elif not legacy()._DEEP_SCAN_ENABLED:
            logger.info("Deep scan disabled by toggle — skipping JS/CSS asset pass.")
        else:
          deep_results = _deep_scan_and_download_assets(
            folder=self.output_folder,
            base_url=self.base_url,
            output_dir=self.output_folder,
            ai_api_key=self.ai_api_key,
            ai_provider=self.ai_provider,
            ai_mode=self.ai_mode,
            ai_budget=self.ai_budget,
          )
          self._register_deep_scan_results(deep_results)

    def _register_deep_scan_results(self, results: Optional[Dict[str, str]]) -> None:
        """Seed the normal asset cache with files saved by deep-scan.

        Deep-scan writes files directly because it must discover assets inside
        bundles.  Without registering its URL map, the later localization
        pass sees the original remote URL and downloads the same file again.
        """
        if not results:
            return
        with self._lock:
            for url, rel_path in results.items():
                full = self._normalize_remote_url(str(url), self.base_url)
                if not full or not rel_path:
                    continue
                local = _safe_join(self.output_folder, str(rel_path).replace("/", os.sep))
                if not os.path.isfile(local):
                    continue
                self._downloaded[full] = local
                cache_key = self._normalize_cache_key(full)
                if cache_key != full:
                    self._downloaded[cache_key] = local
                abs_local = os.path.abspath(local)
                self._source_for_local.setdefault(abs_local, full)
                kind = self._kind_from(full)
                item = {
                    "url": full,
                    "local": os.path.relpath(local, self.output_folder).replace("\\", "/"),
                    "kind": kind,
                }
                if item not in self._success_items:
                    self._success_items.append(item)

    def validate_integrity(self) -> Dict[str, List[str]]:
        """
        Walk downloaded HTML/CSS/JS and verify concrete local file references.

        This intentionally uses context-aware extractors instead of looking for
        every occurrence of words such as ``href`` or ``url``.  The latter
        mistakes ordinary JavaScript (``location.href``/``toDataURL()``) and
        application routes for missing files on modern sites.
        Returns {"missing": [...], "ok": [...]}
        """
        missing_refs: Set[str] = set()
        ok_refs: Set[str] = set()
        asset_extensions = (
            IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS |
            FONT_EXTENSIONS | SCRIPT_EXTENSIONS | STYLE_EXTENSIONS |
            {".json", ".txt", ".zip", ".wasm", ".webmanifest", ".html", ".htm"}
        )
        asset_link_rels = {
            "stylesheet", "icon", "shortcut", "preload", "prefetch",
            "modulepreload", "manifest", "apple-touch-icon",
        }
        js_static_ref_re = re.compile(
            r'(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\bnew\s+URL\s*\(\s*|'
            r'\b(?:src|href|poster)\s*=\s*)'
            r'(?P<quote>["\'])(?P<url>[^"\']+)(?P=quote)',
            re.IGNORECASE,
        )

        def _is_local(ref: str) -> bool:
            value = (ref or "").strip().strip("'\"")
            lowered = value.lower()
            return bool(value) and not lowered.startswith((
                "http://", "https://", "//", "data:", "blob:",
                "javascript:", "mailto:", "tel:", "#",
            ))

        def _has_file_extension(ref: str) -> bool:
            try:
                return pathlib.PurePosixPath(urlparse(ref).path).suffix.lower() in asset_extensions
            except (TypeError, ValueError):
                return False

        def _record(refs: Set[str], ref: object) -> None:
            if not isinstance(ref, str):
                return
            value = ref.strip().strip("'\"")
            if _is_local(value):
                refs.add(value)

        def _local_candidate(owner: str, ref: str) -> str:
            clean = unquote(ref.split("?", 1)[0].split("#", 1)[0])
            if clean.startswith("/"):
                return os.path.normpath(os.path.join(self.output_folder, clean.lstrip("/\\")))
            return os.path.normpath(os.path.join(os.path.dirname(owner), clean))

        # A runtime observer may retain an extra stylesheet that no archived
        # page actually links. Missing dependencies in such an orphan must not
        # make the usable archive fail integrity. Follow stylesheet imports
        # starting from every HTML entry point instead.
        reachable_styles: Set[str] = set()
        style_queue: List[str] = []
        for html_path in pathlib.Path(self.output_folder).rglob("*.htm*"):
            try:
                html_soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
                for link in html_soup.find_all("link", href=True):
                    rels = {str(item).lower() for item in (link.get("rel") or [])}
                    href = str(link.get("href") or "")
                    if "stylesheet" in rels and _is_local(href):
                        candidate = _local_candidate(str(html_path), href)
                        if os.path.isfile(candidate) and candidate not in reachable_styles:
                            reachable_styles.add(candidate)
                            style_queue.append(candidate)
            except Exception as exc:
                logger.debug("Unable to seed reachable styles from %s: %s", html_path, exc)
        while style_queue:
            css_path = style_queue.pop()
            try:
                css_text = pathlib.Path(css_path).read_text(encoding="utf-8", errors="ignore")
                for match in self._css_import_re.finditer(css_text):
                    ref = match.group(1).strip().strip("'\"")
                    if _is_local(ref):
                        candidate = _local_candidate(css_path, ref)
                        if os.path.isfile(candidate) and candidate not in reachable_styles:
                            reachable_styles.add(candidate)
                            style_queue.append(candidate)
            except Exception as exc:
                logger.debug("Unable to follow stylesheet imports from %s: %s", css_path, exc)

        for root, _, files in os.walk(self.output_folder):
            _raise_if_cancelled()
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".html", ".htm", ".css", ".js", ".mjs"}:
                    continue
                local_path = os.path.join(root, name)
                if ext == ".css" and os.path.normpath(local_path) not in reachable_styles:
                    continue
                try:
                    text = pathlib.Path(local_path).read_text(encoding="utf-8", errors="ignore")
                    refs: Set[str] = set()

                    if ext in {".html", ".htm"}:
                        soup = BeautifulSoup(text, "html.parser")
                        for tag in soup.find_all(True):
                            for attr in ("src", "poster"):
                                _record(refs, tag.get(attr))
                            srcset = tag.get("srcset") or tag.get("imagesrcset")
                            if isinstance(srcset, str):
                                for candidate in srcset.split(","):
                                    _record(refs, candidate.strip().split()[0] if candidate.strip() else "")
                            style = tag.get("style")
                            if isinstance(style, str):
                                for match in self._css_url_re.finditer(style):
                                    _record(refs, match.group(1))

                        for link in soup.find_all("link", href=True):
                            rels = {str(item).lower() for item in (link.get("rel") or [])}
                            href = str(link.get("href") or "")
                            if rels & asset_link_rels or _has_file_extension(href):
                                _record(refs, href)

                        for anchor in soup.find_all("a", href=True):
                            href = str(anchor.get("href") or "")
                            if anchor.has_attr("data-cyoa-local-route") or urlparse(href).path.lower().endswith((".html", ".htm")):
                                _record(refs, href)

                        for style_tag in soup.find_all("style"):
                            css = style_tag.get_text(" ", strip=False)
                            for match in self._css_url_re.finditer(css):
                                _record(refs, match.group(1))
                            for match in self._css_import_re.finditer(css):
                                _record(refs, match.group(1))

                    elif ext == ".css":
                        for pattern in (self._css_url_re, self._css_import_re):
                            for match in pattern.finditer(text):
                                _record(refs, match.group(1))
                    else:
                        # Validate only references in executable URL contexts.
                        # A random string ending in .js inside a minified error
                        # message or an optional Node fallback is not a browser
                        # file dependency.
                        for match in js_static_ref_re.finditer(text):
                            candidate = match.group("url")
                            if "${" not in candidate and _has_file_extension(candidate):
                                _record(refs, candidate)

                    # Covers applications that build paths at runtime, e.g.
                    # ``imageSrc + imagesToLoad[i]``.
                    for inferred in _infer_dynamic_asset_paths(text).values():
                        for ref in inferred:
                            _record(refs, ref)

                    source_rel = os.path.relpath(local_path, self.output_folder)
                    for ref in refs:
                        clean_ref = ref.split("?", 1)[0].split("#", 1)[0]
                        if not clean_ref:
                            continue
                        if clean_ref.startswith("/"):
                            abs_ref = os.path.normpath(os.path.join(self.output_folder, clean_ref.lstrip("/\\")))
                        else:
                            abs_ref = os.path.normpath(os.path.join(root, clean_ref))

                        decoded_ref = unquote(clean_ref)
                        if decoded_ref.startswith("/"):
                            decoded_abs_ref = os.path.normpath(os.path.join(self.output_folder, decoded_ref.lstrip("/\\")))
                        else:
                            decoded_abs_ref = os.path.normpath(os.path.join(root, decoded_ref))

                        # Bundlers such as Turbopack keep chunk identifiers like
                        # ``static/chunks/x.js`` inside a runtime file and join
                        # them to their own public root at execution time.
                        root_abs_ref = os.path.normpath(os.path.join(self.output_folder, clean_ref.lstrip("/\\")))
                        decoded_root_abs_ref = os.path.normpath(os.path.join(self.output_folder, decoded_ref.lstrip("/\\")))
                        top_segment = pathlib.Path(source_rel).parts[0] if pathlib.Path(source_rel).parts else ""
                        public_root_abs_ref = os.path.normpath(
                            os.path.join(self.output_folder, top_segment, clean_ref.lstrip("/\\"))
                        )
                        decoded_public_root_abs_ref = os.path.normpath(
                            os.path.join(self.output_folder, top_segment, decoded_ref.lstrip("/\\"))
                        )

                        label = f"{source_rel} → {ref}"
                        if any(os.path.exists(path) for path in (
                            abs_ref, decoded_abs_ref, root_abs_ref, decoded_root_abs_ref,
                            public_root_abs_ref, decoded_public_root_abs_ref,
                        )):
                            ok_refs.add(label)
                        else:
                            missing_refs.add(label)
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in validate_integrity: %s", _ignored_exc)

        missing = sorted(missing_refs)
        ok = sorted(ok_refs)

        if missing:
            logger.warning(
                f"Integrity check: {len(missing)} missing file reference(s), "
                f"{len(ok)} OK"
            )
            for m in missing[:10]:
                logger.warning(f"  MISSING: {m}")
            if len(missing) > 10:
                logger.warning(f"  … and {len(missing)-10} more. See backup_report.txt")
        else:
            logger.info(f"Integrity check: all {len(ok)} file references OK")

        return {"missing": missing, "ok": ok}



    def localize_existing_text_assets(self) -> None:
        """Second-pass scan for index.html/css/js already downloaded."""
        for root, _, files in os.walk(self.output_folder):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".html", ".css", ".js", ".mjs"}:
                    continue
                local_path = os.path.join(root, name)
                source_url = self._source_for_local.get(os.path.abspath(local_path), self.start_url)
                try:
                    text = pathlib.Path(local_path).read_text(encoding="utf-8", errors="ignore")
                    if ext == ".css":
                        updated = self._process_css(text, source_url, local_path)
                    elif ext in {".js", ".mjs"}:
                        updated = self._process_js(text, source_url, local_path)
                    else:
                        updated = self._rewrite_direct_urls(text, source_url, local_path)
                    if updated != text:
                        atomic_write_text(local_path, updated)
                        logger.info(f"  Re-analysed: {os.path.relpath(local_path, self.output_folder)}")
                except DownloadCancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"  Failed to analyse {local_path}: {e}")

    def _headers_for(self, url: str) -> Dict[str, str]:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else self.base_origin + "/"
        headers = dict(self.session.headers)
        headers.update({"Referer": base, "Origin": base.rstrip("/")})
        return headers

    def _fetch(self, url: str) -> Optional[requests.Response]:
        headers = self._headers_for(url)
        try:
            _raise_if_cancelled()
            r = fetch_response(url, extra_headers=headers, timeout=20, as_bytes=False, stream=True)
            if r is None:
                self._failed_items.append({"url": url, "error": "request failed"})
                return None
            return r
        except DownloadCancelledError:
            # Cancellation is control flow, not a failed asset. Do not turn it
            # into a retryable/recorded network failure.
            raise
        except requests.exceptions.SSLError:
            err = f"TLS certificate verification failed: {url}"
            logger.warning(f"  {err}")
            self._failed_items.append({"url": url, "error": err})
            return None
        except requests.exceptions.ConnectionError as e:
            err = str(e).lower()
            if "connection reset" in err or "econnreset" in err:
                msg = f"Connection reset: {url}"
            elif "name or service not known" in err or "nodename" in err:
                msg = f"DNS error (domain tidak ditemukan): {url}"
            else:
                msg = f"Could not fetch {url}: {e}"
            logger.warning(f"  {msg}")
            self._failed_items.append({"url": url, "error": msg})
            return None
        except RecursionError:
            # Circular CSS/JS import chain — sentinel in _download_asset prevents
            # true infinite loop, but deep chains may still overflow stack.
            logger.warning(f"  Circular dependency (skipped): {url}")
            return None
        except Exception as e:
            err = str(e)
            logger.warning(f"  Could not fetch {url}: {err}")
            self._failed_items.append({"url": url, "error": err})
            return None

    def _normalize_remote_url(self, url: str, referrer_url: Optional[str] = None) -> Optional[str]:
        if not url:
            return None
        url = url.strip().strip('"\'')
        lowered = url.lower()
        if lowered.startswith(("data:", "javascript:", "mailto:", "file:", "ftp:", "blob:", "chrome:", "about:")) or url.startswith("#"):
            return None
        if url.startswith("//"):
            scheme = urlparse(referrer_url or self.start_url).scheme or "https"
            return f"{scheme}:{url}" if scheme in {"http", "https"} else None
        try:
            explicit_scheme = urlparse(url).scheme.lower()
        except ValueError:
            return None
        if explicit_scheme and explicit_scheme not in {"http", "https"}:
            return None
        joined = urljoin(referrer_url, url) if referrer_url else url
        try:
            parsed = urlparse(joined)
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"}:
            return None

        # Next/Image is a proxy whose identity lives in the ``url=`` query.
        # Saving every proxy request as ``/_next/image`` caused collisions and
        # low-resolution variants to overwrite unrelated images. Download the
        # original public asset instead; the HTML is then rewritten to it.
        if parsed.path.rstrip("/").endswith("/_next/image"):
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                if key == "url" and value.startswith(("http://", "https://")):
                    return value
        return joined

    def _normalize_cache_key(self, url: str) -> str:
        """
        Normalize URL for _downloaded cache lookup.
        Strip only known cache-buster parameters. Meaningful query parameters
        (notably Next/Image's ``url``, API selectors, widths and formats) must
        remain part of the identity or unrelated resources collide.
        """
        try:
            p = urlparse(url)
            cache_busters = {
                "v", "ver", "version", "cb", "cache", "cachebust",
                "cache_bust", "cachebuster", "t", "ts", "timestamp", "_", "dpl",
            }
            query = [
                (key, value) for key, value in parse_qsl(p.query, keep_blank_values=True)
                if key.lower() not in cache_busters
            ]
            return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode(query), ""))
        except Exception:
            return url

    def _safe_filename(self, url: str, fallback: str = "asset", ext_hint: str = "") -> str:
        parsed = urlparse(url)
        name = os.path.basename(parsed.path) or fallback
        name = clean_url_path_component(name)
        root, ext = os.path.splitext(name)
        if not ext and ext_hint:
            ext = ext_hint
        if not root:
            root = fallback
        if not ext:
            ext = ".bin"
        # Filesystem name limits: Linux NAME_MAX is 255
        # bytes and Windows paths cap near 260 chars. Very long CDN/bundler
        # basenames previously produced OSError 36 ("File name too long") at
        # save time, so the asset silently failed. Truncate the stem and add a
        # short content-stable hash of the ORIGINAL name so distinct long
        # names can't collide after truncation. Names within the limit are
        # returned unchanged.
        _MAX_NAME = 140
        if len(root) + len(ext) > _MAX_NAME:
            import hashlib as _hl
            digest = _hl.sha1(name.encode("utf-8", "replace")).hexdigest()[:10]
            keep = max(1, _MAX_NAME - len(ext) - 11)  # 11 = "_" + digest
            root = f"{root[:keep]}_{digest}"
        return f"{root}{ext}"

    def _kind_from(self, url: str, content_type: str = "", preferred_kind: str = "") -> str:
        if preferred_kind:
            return preferred_kind
        lower_ct = (content_type or "").lower()
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1]

        if "text/css" in lower_ct or ext in STYLE_EXTENSIONS:
            return "css"
        if "javascript" in lower_ct or ext in SCRIPT_EXTENSIONS:
            return "js"
        if "font" in lower_ct or ext in FONT_EXTENSIONS:
            return "fonts"
        if lower_ct.startswith("image/") or ext in IMAGE_EXTENSIONS:
            return "images"
        if lower_ct.startswith("audio/") or lower_ct.startswith("video/") or ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
            return "media"
        if lower_ct == "application/json" or path.endswith(("project.json", "project.txt", "project.zip")) or ext in {".json", ".txt", ".zip"}:
            return "json"
        if "text/html" in lower_ct or ext in {".html", ".htm"}:
            return "html"
        return "assets"

    def _allocate_local_path(self, url: str, content_type: str = "", preferred_kind: str = "") -> str:
        kind = self._kind_from(url, content_type=content_type, preferred_kind=preferred_kind)
        if kind == "html":
            return self.start_html_local
        if kind == "json" and urlparse(url).path.lower().endswith("project.json"):
            return os.path.join(self.output_folder, "project.json")

        # ── Preserve original relative path from site root ────────────────
        parsed       = urlparse(url)
        start_parsed = urlparse(self.start_url)
        if parsed.netloc == start_parsed.netloc:
            start_dir  = start_parsed.path.rstrip("/") + "/"
            asset_path = unquote(parsed.path)
            if asset_path.startswith(start_dir):
                rel_parts = asset_path[len(start_dir):]   # e.g. "js/shared/components/Foo.js"
            elif asset_path.startswith("/"):
                # Asset is on same domain but above start_dir (e.g. /js/foo.js for start /paradise/)
                # Preserve from root so js/shared/components/ structure is kept
                rel_parts = asset_path.lstrip("/")
            else:
                rel_parts = asset_path

            if rel_parts:
                # Extensionless content is common behind framework endpoints.
                # Give it the browser-appropriate extension before localization.
                rel_root, rel_ext = os.path.splitext(rel_parts)
                if not rel_ext:
                    rel_ext = {
                        "images": ".jpg" if "jpeg" in content_type.lower() else ".png",
                        "css": ".css", "js": ".js", "fonts": ".woff2",
                        "json": ".json",
                    }.get(kind, "")
                    rel_parts = rel_root + rel_ext
                normalized_query = urlparse(self._normalize_cache_key(url)).query
                if normalized_query:
                    rel_root, rel_ext = os.path.splitext(rel_parts)
                    digest = hashlib.sha1(normalized_query.encode("utf-8", "replace")).hexdigest()[:10]
                    rel_parts = f"{rel_root}_{digest}{rel_ext}"
                local_candidate = _safe_join(self.output_folder, rel_parts)
                os.makedirs(os.path.dirname(local_candidate), exist_ok=True)
                local = local_candidate
                root, ext = os.path.splitext(local)
                counter = 1
                while local in self._used_local_paths:
                    local = f"{root}_{counter}{ext}"
                    counter += 1
                if local != local_candidate:
                    logger.warning(
                        f"  Path collision: {os.path.relpath(local_candidate, self.output_folder)} "
                        f"already taken → renamed to {os.path.relpath(local, self.output_folder)}"
                    )
                    self._collision_log.append({
                        "url": url,
                        "original_path": os.path.relpath(local_candidate, self.output_folder).replace("\\", "/"),
                        "saved_as":      os.path.relpath(local, self.output_folder).replace("\\", "/"),
                    })
                self._used_local_paths.add(local)
                return local

        # ── Fallback: cross-domain URL — use type-based flat folder ──────────
        ext_hint = {
            "css": ".css",
            "js": ".js",
            "fonts": ".woff2",
            "images": ".png",
            "json": ".json",
            "media": ".bin",
        }.get(kind, "")
        filename = self._safe_filename(url, fallback=kind[:-1] if kind.endswith("s") else kind, ext_hint=ext_hint)
        normalized_query = urlparse(self._normalize_cache_key(url)).query
        if normalized_query:
            root, ext = os.path.splitext(filename)
            digest = hashlib.sha1(normalized_query.encode("utf-8", "replace")).hexdigest()[:10]
            filename = f"{root}_{digest}{ext}"
        folder = os.path.join(self.output_folder, kind if kind not in {"html", "json"} else "assets")
        os.makedirs(folder, exist_ok=True)

        local = os.path.join(folder, filename)
        root, ext = os.path.splitext(local)
        counter = 1
        while local in self._used_local_paths:
            local = f"{root}_{counter}{ext}"
            counter += 1
        if counter > 1:
            original_local = os.path.join(folder, filename)
            logger.warning(
                f"  Path collision (external): {os.path.relpath(original_local, self.output_folder)} "
                f"already taken → renamed to {os.path.relpath(local, self.output_folder)}"
            )
            self._collision_log.append({
                "url": url,
                "original_path": os.path.relpath(original_local, self.output_folder).replace("\\", "/"),
                "saved_as":      os.path.relpath(local, self.output_folder).replace("\\", "/"),
            })
        self._used_local_paths.add(local)
        return local

    def _rel(self, from_file: str, to_file: str) -> str:
        return os.path.relpath(to_file, os.path.dirname(from_file)).replace("\\", "/")

    def _download_asset(self, url: str, preferred_kind: str = "", referrer_url: Optional[str] = None) -> Optional[str]:
        _raise_if_cancelled()
        full = self._normalize_remote_url(url, referrer_url)
        if not full:
            return None

        # Analytics, feedback widgets, and telemetry are not part of an
        # offline story. Their bootstrap scripts recursively reference
        # generated endpoints and can dominate a mirror with retries/404s.
        host = (urlparse(full).hostname or "").lower()
        if host in self._telemetry_hosts or host.endswith((".sentry.io", ".posthog.com")):
            logger.debug("  Telemetry skipped: %s", full)
            return None

        # SSRF screen on the deep-scan asset chokepoint.
        # A scanned JS/CSS/HTML file from an untrusted site can reference a
        # cross-origin internal host; block it unless same-origin as the page
        # being mirrored (self.start_url) or --allow-internal-hosts is set.
        if _ssrf_block_cross_origin(full, getattr(self, "start_url", "")):
            logger.warning(f"  [SSRF blocked] cross-origin internal host: {full}")
            with self._lock:
                self._downloaded[full] = None
            return None

        with self._lock:
            # Check both full URL and path-only key (strips ?v=cache_buster)
            cache_key = self._normalize_cache_key(full)
            if full in self._downloaded:
                return self._downloaded[full]
            if cache_key != full and cache_key in self._downloaded:
                cached = self._downloaded[cache_key]
                self._downloaded[full] = cached   # alias
                return cached
            # ── Anti-recursion sentinel ────────────────────────────────────
            self._downloaded[full] = None   # sentinel: in-progress

        r = self._fetch(full)
        _raise_if_cancelled()

        # ── JS root-relative fallback ──────────────────────────────
        # Paths in JS/data files like "images/headers/foo.avif" are
        # often intended relative to the page root, NOT the JS file.
        # Example: js/data.js has "images/foo.avif" → wrong resolve is
        #   js/images/foo.avif, correct is images/foo.avif (page root).
        # If the fetch failed AND the raw path is relative AND the
        # referrer was a JS file, retry from page root.
        if r is None and referrer_url and not url.startswith(("http", "//", "data:", "#")):
            ref_path = urlparse(referrer_url).path.lower()
            if ref_path.endswith((".js", ".mjs")):
                # Custom viewers commonly put data paths in a JS bundle but
                # intend them relative to the viewer route, not the domain
                # root.  Preserve the normal root-relative fallback for
                # ordinary sites.
                js_root = self.base_url if self._custom_viewer_route else self.start_url
                alt = self._normalize_remote_url(url, js_root)
                if alt and alt != full:
                    # Check cache first — same raw string may appear multiple times in data.js
                    with self._lock:
                        if alt in self._downloaded:
                            return self._downloaded[alt]
                    r_alt = self._fetch(alt)
                    _raise_if_cancelled()
                    if r_alt:
                        logger.info(f"  JS root-fallback: {url} → {alt}")
                        r    = r_alt
                        full = alt

        if not r:
            return None

        content_type = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        requested_ext = os.path.splitext(urlparse(full).path.lower())[1]
        if content_type.startswith("text/html") and requested_ext in (IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | FONT_EXTENSIONS):
            error = f"Content-Type mismatch for binary asset: {content_type or 'unknown'}"
            logger.warning(f"  {error}: {full}")
            self._failed_items.append({"url": full, "error": error})
            try:
                r.close()
            except Exception as exc:
                logger.debug(f"Response close failed for rejected asset {full}: {exc}")
            return None
        local = self._allocate_local_path(full, content_type=content_type, preferred_kind=preferred_kind)
        abs_local = os.path.abspath(local)
        os.makedirs(os.path.dirname(local), exist_ok=True)

        try:
            if "text/css" in content_type or local.lower().endswith(".css"):
                raw_text = _safe_response_text(r)
                _raise_if_cancelled()
                _throttle_bandwidth(len(r.content))
                content = self._process_css(raw_text, full, local)
                atomic_write_text(local, content)
            elif "javascript" in content_type or local.lower().endswith((".js", ".mjs")):
                raw_text = _safe_response_text(r)
                _raise_if_cancelled()
                _throttle_bandwidth(len(r.content))
                content = self._process_js(raw_text, full, local)
                atomic_write_text(local, content)
            elif "text/html" in content_type or local.lower().endswith((".html", ".htm")):
                html_text = _safe_response_text(r)
                _raise_if_cancelled()
                _throttle_bandwidth(len(r.content))
                self._download_html(full, local_html=local, html_text=html_text)
            else:
                atomic_stream_response_to_file(r, local)
        finally:
            try:
                r.close()
            except Exception as exc:
                logger.debug(f"Response close failed for {full}: {exc}")

        with self._lock:
            self._downloaded[full] = local
            # Also register path-only key so query-string variants hit cache
            ck = self._normalize_cache_key(full)
            if ck != full:
                self._downloaded[ck] = local
            self._source_for_local[abs_local] = full

        self._success_items.append({
            "url": full,
            "local": os.path.relpath(local, self.output_folder).replace("\\", "/"),
            "kind": self._kind_from(full, content_type=content_type, preferred_kind=preferred_kind),
        })
        logger.info(f"  Asset: {os.path.relpath(local, self.output_folder)}")
        return local

    def download_asset(self, url: str, preferred_kind: str = "", referrer_url: Optional[str] = None) -> Optional[str]:
        """Public archive-extension hook that retains the normal safety path."""
        return self._download_asset(url, preferred_kind=preferred_kind, referrer_url=referrer_url)

    def download_html_page(self, url: str, local_html: str, html_text: str) -> None:
        """Localize one explicitly mapped route page."""
        self._download_html(url, local_html=local_html, html_text=html_text)

    def _asset_kind_from_path(self, candidate: str) -> str:
        try:
            path = urlparse(candidate).path.lower()
        except ValueError:
            return "assets"
        ext = os.path.splitext(path)[1]
        if path.endswith(("project.json", "project.txt", "project.zip")) or ext in {".json", ".txt", ".zip"}:
            return "json"
        if ext in FONT_EXTENSIONS:
            return "fonts"
        if ext in IMAGE_EXTENSIONS:
            return "images"
        if ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
            return "media"
        if ext in STYLE_EXTENSIONS:
            return "css"
        if ext in SCRIPT_EXTENSIONS:
            return "js"
        return "assets"

    def _should_download_from_text(self, candidate: str) -> bool:
        c = candidate.strip().strip('"\'')
        if not c or c.startswith(("data:", "javascript:", "mailto:", "#")):
            return False
        if "w3.org/" in c:
            return False
        try:
            path = urlparse(c).path.lower()
        except ValueError:
            return False
        if path.endswith(("project.json", "project.txt", "project.zip")):
            return True
        ext = os.path.splitext(path)[1]
        if ext in FONT_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | STYLE_EXTENSIONS | SCRIPT_EXTENSIONS | {".json", ".txt", ".zip"}:
            return True
        return False

    def _existing_local_asset(self, reference: str, owner_path: str) -> bool:
        """Return True when a relative asset reference already exists locally."""
        value = str(reference or "").strip().strip('"\'')
        if not value or value.startswith(("/", "//")):
            return False
        try:
            parsed = urlparse(value)
            if parsed.scheme or parsed.netloc:
                return False
            relative = unquote(parsed.path)
            candidate = os.path.abspath(
                os.path.normpath(os.path.join(os.path.dirname(owner_path), relative))
            )
            root = os.path.abspath(self.output_folder)
            return os.path.commonpath([root, candidate]) == root and os.path.isfile(candidate)
        except (OSError, ValueError):
            return False

    def _rewrite_direct_urls(self, text: str, referrer_url: str, local_text_path: str) -> str:
        dynamic_asset_tokens = _infer_dynamic_asset_paths(text)

        def repl(m: re.Match) -> str:
            original = m.group("url")
            # JSON-escaped slashes ("img\/x.png") reached
            # _download_asset verbatim, producing an unfetchable URL, so those
            # assets were silently skipped during website localization (same
            # bug class as rev6/rev7). The rewrite replaces the whole quoted
            # token with a local relative path, so unescaping here is lossless.
            if "\\/" in original:
                original = original.replace("\\/", "/")
            # Keep filename-array entries unchanged when JavaScript adds a
            # separate image/asset base variable at runtime. The deep scanner
            # downloads their inferred combined paths; rewriting the token
            # itself would produce ``image/image/...`` in the browser.
            if original in dynamic_asset_tokens:
                return m.group(0)
            if not self._should_download_from_text(original):
                return m.group(0)
            # Skip values already localized by an earlier
            # rewrite pass. _process_css runs @import/url() rewriting BEFORE this
            # direct-URL pass, so relative paths like "../assets/bg.png" may
            # already point to a downloaded local file. Re-resolving them against
            # the referrer would produce a bogus remote URL and trigger a
            # redundant fetch (and a mis-rewrite risk if that URL resolves to
            # different content). If the relative candidate already resolves to
            # an existing local file, leave it untouched.
            if not urlparse(original).scheme and not original.startswith("//"):
                try:
                    candidate_local = os.path.normpath(
                        os.path.join(os.path.dirname(local_text_path), original.split("?", 1)[0])
                    )
                    if os.path.isfile(candidate_local):
                        return m.group(0)
                except Exception:
                    pass
            local = self._download_asset(
                original,
                preferred_kind=self._asset_kind_from_path(original),
                referrer_url=referrer_url,
            )
            if not local:
                return m.group(0)
            rel = self._rel(local_text_path, local)
            return f'{m.group("quote")}{rel}{m.group("quote")}'
        return self._quoted_asset_re.sub(repl, text)

    def _process_css(self, css: str, css_url: str, css_local: str) -> str:
        def repl_import(m: re.Match) -> str:
            raw = m.group(1).strip().strip('"\'')
            if self._existing_local_asset(raw, css_local):
                return m.group(0)
            full = self._normalize_remote_url(raw, css_url)
            if not full:
                return m.group(0)
            local = self._download_asset(full, preferred_kind="css", referrer_url=css_url)
            if not local:
                return m.group(0)
            return f'@import url("{self._rel(css_local, local)}")'

        def repl_url(m: re.Match) -> str:
            raw = m.group(1).strip().strip('"\'')
            if self._existing_local_asset(raw, css_local):
                return m.group(0)
            full = self._normalize_remote_url(raw, css_url)
            if not full:
                return m.group(0)
            kind = self._asset_kind_from_path(full)
            local = self._download_asset(full, preferred_kind=kind, referrer_url=css_url)
            if not local:
                return m.group(0)
            return f'url("{self._rel(css_local, local)}")'

        css = self._css_import_re.sub(repl_import, css)
        css = self._css_url_re.sub(repl_url, css)
        css = self._rewrite_direct_urls(css, css_url, css_local)
        return css

    # Patterns that identify webpack/Vite application bundles.
    # These files must NOT have their internal paths rewritten —
    # the bundle's own module references (project.json, chunk paths, etc.)
    # are resolved at runtime by webpack, not by URL.

    # webpack hashes: lowercase hex, 8-20 chars  e.g. app.c533aa25.js
    # Vite hashes (dot):        base62, 6-12 chars  e.g. app.B6d7tc9y.js
    # Vite hashes (underscore): Neocities variant   e.g. app_BuGW6RFa.js
    # CYOA Manager:   working.js (ICC Original 1.4MB full bundle)
    _APP_BUNDLE_RE = re.compile(
        r'(?:^|/)(?:app|main|index|chunk-vendors?|vendors?|runtime|polyfills?|core|working)'
        r'(?:[._-][a-zA-Z0-9_-]{4,})?(?:-legacy)?(?:[._-][a-zA-Z0-9_-]{4,})?'
        r'\.m?js$',
        re.IGNORECASE,
    )

    # ── Dynamic loader patterns ────────────────────────────────────────────
    # JS files that compute asset URLs dynamically at runtime.
    # These must NOT be path-rewritten — URLs are computed by the browser,
    # not as literal strings we can safely replace.
    # Each tuple: (detection_pattern, url_extractor_pattern, url_base_func)
    _DYNAMIC_LOADER_PATTERNS = [
        # ICC Plus v2 core.js: basePath = new URL('../', currentScript.src)
        # Extracts: basePath + 'relpath'
        (
            re.compile(r'basePath\s*=\s*new URL\(["\']\.\./', re.IGNORECASE),
            re.compile(r"""basePath\s*\+\s*['"]([^'"]+)['"]"""),
            lambda js_url: __import__('urllib.parse', fromlist=['urljoin']).urljoin(js_url, "../"),
        ),
        # Generic: __webpack_public_path__ / __publicPath__
        (
            re.compile(r'__webpack_public_path__|__publicPath__'),
            re.compile(r"""['"]([^'"]+\.(?:js|css|mjs))['"]"""),
            lambda js_url: __import__('urllib.parse', fromlist=['urljoin']).urljoin(js_url, "./"),
        ),
    ]

    def _detect_dynamic_loader(self, js: str) -> Optional[tuple]:
        """
        Detect if a JS file is a dynamic asset loader (like ICC Plus v2 core.js).
        Returns (extractor_re, base_url_fn) if detected, else None.
        Prevents incorrect URL rewriting for files that compute paths at runtime.
        """
        for detect_re, extract_re, base_fn in self._DYNAMIC_LOADER_PATTERNS:
            if detect_re.search(js):
                return (extract_re, base_fn)
        return None

    def _is_app_bundle(self, js_url: str) -> bool:
        """True for webpack/Vite bundles that must NOT be path-rewritten."""
        path = urlparse(js_url).path
        return bool(self._APP_BUNDLE_RE.search(path))

    def _process_js(self, js: str, js_url: str, js_local: str) -> str:
        """
        Rewrite asset URLs inside a JS file.

        Guard 1 — Dynamic loaders (core.js, webpack bootstrap, etc.):
          Files that compute asset URLs at runtime (basePath, __webpack_public_path__,
          import.meta.url, etc.) are detected via _detect_dynamic_loader().
          We download the assets they reference using the correct server URL,
          then return the file UNCHANGED so the browser computes paths correctly.

        Guard 2 — App bundles (app.*.js, chunk-vendors.*.js, etc.):
          Webpack/Vite bundles are skipped entirely — their internal paths are
          resolved by the module bundler, not as literal filesystem URLs.
        """
        # Guard 1: dynamic loader
        loader = self._detect_dynamic_loader(js)
        if loader:
            extract_re, base_fn = loader
            base_url = base_fn(js_url)
            logger.info(f"  Dynamic loader detected: {js_url.split('/')[-1]} (base: {base_url})")
            for m in extract_re.finditer(js):
                asset_rel = m.group(1)
                asset_url = urljoin(base_url, asset_rel)
                kind      = self._asset_kind_from_path(asset_url)
                self._download_asset(asset_url, preferred_kind=kind, referrer_url=js_url)
            return js  # UNCHANGED — browser resolves paths at runtime

        # Guard 2: app bundle
        if self._is_app_bundle(js_url):
            logger.debug(f"  Skip JS rewrite (app bundle): {js_url.split('/')[-1]}")
            return js

        return self._rewrite_direct_urls(js, js_url, js_local)

    def _rewrite_css_url(self, m: "re.Match", css_url: str, css_local: str) -> str:
        """Rewrite a single CSS url() match to a local path."""
        raw = m.group(1).strip().strip('"\'')
        if self._existing_local_asset(raw, css_local):
            return m.group(0)
        full = self._normalize_remote_url(raw, css_url)
        if not full:
            return m.group(0)
        kind  = self._asset_kind_from_path(full)
        local = self._download_asset(full, preferred_kind=kind, referrer_url=css_url)
        if not local:
            return m.group(0)
        return f'url("{self._rel(css_local, local)}")'

    def _set_attr_local(self, tag, attr: str, page_url: str, local_html: str, preferred_kind: str = "") -> None:
        value = tag.get(attr)
        if not value:
            return
        if attr in {"srcset", "imagesrcset"}:
            # data: URIs commonly contain commas (inline SVG,
            # base64). A naive value.split(",") shreds them into garbage pieces,
            # destroying the data URI and mis-parsing the following candidate.
            # Split on commas only when NOT inside a data: URI. The srcset grammar
            # separates candidates by comma + whitespace; a data: URI candidate
            # is left intact and passed through unchanged (it needs no download).
            def _split_srcset(s: str) -> List[str]:
                out, buf, i, n = [], [], 0, len(s)
                while i < n:
                    # Detect start of a data: URI at a candidate boundary.
                    rest = s[i:]
                    if rest.lstrip().lower().startswith("data:"):
                        # consume up to the comma+space that ends this candidate,
                        # i.e. a comma followed by whitespace (descriptor sep) OR
                        # end of string. Commas inside the data URI have no space.
                        # Find the next ", " sequence (comma + whitespace).
                        j = i
                        while j < n:
                            if s[j] == "," and (j + 1 >= n or s[j + 1].isspace()):
                                break
                            j += 1
                        out.append(s[i:j].strip())
                        i = j + 1
                        continue
                    if s[i] == ",":
                        out.append("".join(buf).strip())
                        buf = []
                        i += 1
                        continue
                    buf.append(s[i])
                    i += 1
                if buf:
                    out.append("".join(buf).strip())
                return [c for c in out if c]

            parts = []
            for chunk in _split_srcset(value):
                bits = chunk.strip().split()
                if not bits:
                    continue
                asset = bits[0]
                suffix = " " + " ".join(bits[1:]) if len(bits) > 1 else ""
                # data: URIs are already inline — keep them verbatim, no download.
                if asset.lower().startswith("data:"):
                    parts.append(chunk.strip())
                    continue
                local = self._download_asset(asset, preferred_kind=preferred_kind, referrer_url=page_url)
                if local:
                    parts.append(self._rel(local_html, local) + suffix)
                else:
                    # Preserve an explicit online fallback, but never leave a
                    # failed relative URL that looks like a missing local file
                    # inside the offline package.
                    remote = self._normalize_remote_url(asset, page_url)
                    parts.append((remote or asset) + suffix)
            tag[attr] = ", ".join(parts)
            return

        local = self._download_asset(value, preferred_kind=preferred_kind, referrer_url=page_url)
        if not local:
            # Fallback: maybe the same file was already downloaded from a different path
            # (e.g. <link rel="preload" href="js/polyfills.js"> downloaded it, but
            #  <script src="polyfills.js"> references it without the js/ prefix).
            # Search _downloaded cache for any URL whose basename matches.
            basename = value.rstrip("/").split("?")[0].split("/")[-1]
            if basename:
                for cached_url, cached_local in self._downloaded.items():
                    if cached_local and cached_url.split("?")[0].split("/")[-1] == basename:
                        local = cached_local
                        logger.debug(f"  Basename fallback: {value!r} → {os.path.relpath(local, self.output_folder)}")
                        break
        if local:
            tag[attr] = self._rel(local_html, local)
        else:
            # The failure is recorded in backup_report/manifest. Keeping an
            # absolute URL here accurately marks it as an unresolved external
            # dependency and avoids a misleading broken local reference.
            remote = self._normalize_remote_url(value, page_url)
            if remote:
                tag[attr] = remote

    def _download_html(self, url: str, local_html: Optional[str] = None, html_text: Optional[str] = None) -> None:
        _raise_if_cancelled()
        local_html = local_html or self.start_html_local
        abs_local = os.path.abspath(local_html)

        if html_text is None:
            r = self._fetch(url)
            if not r:
                if self.archive_strategy in {"smart", "browser", "auto"}:
                    try:
                        from ..network.browser import _fetch_headless
                        from ..project.parse import try_decode_bytes
                        raw = _fetch_headless(url)
                        html_text = try_decode_bytes(raw) if raw else None
                    except DownloadCancelledError:
                        raise
                    except Exception as exc:
                        logger.debug("Headless entry fetch failed for %s: %s", url, exc)
                if not html_text:
                    raise RuntimeError(f"Could not download entry HTML: {url}")
            else:
                try:
                    html_text = _safe_response_text(r)
                finally:
                    try:
                        r.close()
                    except Exception:
                        pass

        soup = BeautifulSoup(html_text, "html.parser")
        _raise_if_cancelled()
        os.makedirs(os.path.dirname(local_html), exist_ok=True)

        # Some hand-written CYOA viewers are served from an extensionless
        # route (for example ``/drukhari``) but store all relative assets
        # below that route (``/drukhari/js/...``).  ``urljoin`` quite
        # correctly treats the route as a document and would otherwise
        # resolve those assets at the domain root.  Keep the fetched page
        # URL intact, but use the directory route as the asset referrer for
        # this custom-viewer shape.
        html_lower = str(html_text or "").lower()
        asset_page_url = url
        if (
            url == self.start_url
            and not url.rstrip().endswith("/")
            and (
                ('id="cyoa-container"' in html_lower and "game_data" in html_lower)
                or ('id="bg-music"' in html_lower and "point-bar" in html_lower)
            )
        ):
            self._custom_viewer_route = True
            asset_page_url = self.base_url
            logger.info("  Custom viewer route detected; resolving entry assets below %s", asset_page_url)

        for tag in soup.find_all("link"):
            _raise_if_cancelled()
            rel_values = {str(v).lower() for v in (tag.get("rel") or [])}
            # Next.js image preloads commonly have imagesrcset without href.
            # It needs the same optimizer unwrapping/localization as img srcset.
            if tag.get("imagesrcset"):
                self._set_attr_local(tag, "imagesrcset", asset_page_url, local_html, preferred_kind="images")
            href = tag.get("href")
            if not href or href.startswith(("data:", "javascript:", "#", "mailto:")):
                continue

            # Resolve absolute-path hrefs (e.g. /favicon.ico) against page origin
            # so they download from correct domain even when we're in a subpath
            if href.startswith("/") and not href.startswith("//"):
                parsed_page = urlparse(url)
                href_resolved = f"{parsed_page.scheme}://{parsed_page.netloc}{href}"
                tag["href"] = href_resolved
                href = href_resolved

            href_lower = href.lower().split("?")[0]  # strip query string for ext check

            if "stylesheet" in rel_values:
                self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="css")

            elif rel_values & {"icon", "button control", "apple-touch-icon",
                               "apple-touch-icon-precomposed", "mask-icon",
                               "image_src"}:
                self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="images")

            elif "manifest" in rel_values:
                # PWA manifest.json — download as json asset
                self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="json")

            elif rel_values & {"preload", "prefetch", "modulepreload"}:
                # Preload/prefetch: download based on 'as' attribute or extension
                as_val = (tag.get("as") or "").lower()
                if as_val in ("image", "fetch") or any(
                    href_lower.endswith(ext)
                    for ext in IMAGE_EXTENSIONS | {".ico"}
                ):
                    self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="images")
                elif as_val == "font" or any(href_lower.endswith(ext) for ext in FONT_EXTENSIONS):
                    self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="fonts")
                elif as_val in ("script", "worker") or href_lower.endswith((".js", ".mjs")):
                    self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="js")
                elif as_val == "style" or href_lower.endswith(".css"):
                    self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="css")

            elif href_lower.endswith("project.json"):
                self._set_attr_local(tag, "href", url, local_html, preferred_kind="json")

            else:
                # Catch-all: any <link href="..."> where href looks like a downloadable asset
                # (regardless of rel value — e.g. rel="license" href="banner.png")
                ext = os.path.splitext(href_lower)[1]
                if ext in IMAGE_EXTENSIONS | FONT_EXTENSIONS | {".ico", ".webmanifest"}:
                    kind = "fonts" if ext in FONT_EXTENSIONS else "images"
                    self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind=kind)
                elif ext in SCRIPT_EXTENSIONS | {".js", ".mjs"}:
                    self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="js")


        for tag in soup.find_all("script", src=True):
            _raise_if_cancelled()
            src_val = tag.get("src", "")
            if "youtube.com/iframe_api" in src_val or "youtube-nocookie.com/iframe_api" in src_val:
                stub_local = self._ensure_youtube_iframe_api_stub()
                tag["src"] = self._rel(local_html, stub_local)
                continue
            self._set_attr_local(tag, "src", asset_page_url, local_html, preferred_kind="js")

        # Replace YouTube <iframe> embeds with an offline placeholder.
        # Direct YouTube iframes cannot work offline regardless of the JS stub —
        # they require a live connection to youtube.com.
        for tag in soup.find_all("iframe"):
            iframe_src = tag.get("src", "") or tag.get("data-src", "")
            if _YOUTUBE_URL_RE.search(iframe_src):
                video_id = ""
                m = re.search(r'/embed/([A-Za-z0-9_-]+)', iframe_src)
                if m:
                    video_id = m.group(1)
                yt_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else iframe_src
                w = tag.get("width", "560")
                h = tag.get("height", "315")
                placeholder_html = (
                    f'<div style="width:{w}px;height:{h}px;background:#111;color:#aaa;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'flex-direction:column;font-family:sans-serif;border-radius:6px;'
                    f'border:1px solid #333;box-sizing:border-box;">'
                    f'<span style="font-size:32px">▶</span>'
                    f'<span style="margin-top:8px;font-size:12px">YouTube (offline unavailable)</span>'
                    f'<a href="{yt_url}" target="_blank" '
                    f'style="margin-top:6px;font-size:11px;color:#4af">Open on YouTube</a>'
                    f'</div>'
                )
                tag.replace_with(BeautifulSoup(placeholder_html, "html.parser"))
                logger.info(f"  YouTube iframe replaced with offline placeholder: {yt_url}")
                continue

        for tag in soup.find_all(["img", "audio", "video", "source"]):
            if tag.get("src"):
                kind = "images" if tag.name == "img" else "media"
                self._set_attr_local(tag, "src", asset_page_url, local_html, preferred_kind=kind)
            if tag.get("srcset"):
                self._set_attr_local(tag, "srcset", asset_page_url, local_html, preferred_kind="images")
            if tag.get("poster"):
                self._set_attr_local(tag, "poster", asset_page_url, local_html, preferred_kind="images")

        # ── Inline <style> @font-face and url() ─────────────────────────────
        # Fonts declared directly in <style> tags (not linked CSS) are missed
        # unless we process them explicitly here.
        for style_tag in soup.find_all("style"):
            raw_css = style_tag.string or ""
            if not raw_css.strip():
                continue
            # Process as if it were a CSS file at the page URL
            new_css = self._process_css(raw_css, asset_page_url, local_html)
            if new_css != raw_css:
                style_tag.string = new_css

        # ── Inline style="" attributes (background-image, etc.) ─────────────
        for tag in soup.find_all(True, style=True):
            raw_style = tag.get("style", "")
            if raw_style and "url(" in raw_style:
                new_style = self._css_url_re.sub(
                    lambda m: self._rewrite_css_url(m, asset_page_url, local_html),
                    raw_style,
                )
                if new_style != raw_style:
                    tag["style"] = new_style

        html_output = str(soup)
        # NOTE: do NOT call _rewrite_direct_urls(html_output) here.
        # All tag attributes have already been rewritten by _set_attr_local above.
        # Calling _rewrite_direct_urls on str(soup) would try to re-download
        # the already-localized relative paths (e.g. "images/favicon.ico"),
        # resolve them against the page URL (wrong!), and corrupt the HTML.
        # The second pass (localize_existing_text_assets) handles any missed URLs.
        pathlib.Path(local_html).write_text(html_output, encoding="utf-8")

        with self._lock:
            self._downloaded[url] = local_html
            self._source_for_local[abs_local] = url

        logger.info(f"  Page: {os.path.relpath(local_html, self.output_folder)}")

    # ── Methods that were previously monkey-patched — now proper class methods ──

    def _ensure_youtube_iframe_api_stub(self) -> str:
        stub_local = os.path.join(self.output_folder, "js", "youtube-iframe-api-stub.js")
        os.makedirs(os.path.dirname(stub_local), exist_ok=True)
        # Always overwrite — ensures new HTML5 audio version replaces old dummy stub
        stub = r"""(function(){
  if (window.YT && window.YT.Player && window.YT.__cyoa_stub__) return;

  function _isLocalAudio(id){
    return typeof id === 'string' && (
      id.indexOf('/') !== -1 ||
      id.indexOf('.mp3') !== -1 || id.indexOf('.ogg') !== -1 ||
      id.indexOf('.wav') !== -1 || id.indexOf('.m4a') !== -1 ||
      id.indexOf('.aac') !== -1 || id.indexOf('.opus') !== -1
    );
  }

  function AudioPlayer(id, options){
    // ICC Plus uses "bgm-player" in newer versions, "bgm" in older ones
    this._el     = typeof id === 'string' ? document.getElementById(id) : id;
    this._opts   = options || {};
    this._state  = -1;
    this._volume = 100;
    this._muted  = false;
    this._audio  = null;
    this._events = this._opts.events || {};
    this._videoData  = {video_id:'', title:''};
    this.playerInfo  = {videoData: this._videoData};
    // Expose on window so ICC Plus can find it by element ID
    if (typeof id === 'string' && id) window['__ytplayer_'+id] = this;
    var self = this;
    setTimeout(function(){
      try { if (typeof self._events.onReady === 'function') self._events.onReady({target:self}); } catch(e){}
    }, 0);
  }

  AudioPlayer.prototype._loadAudio = function(videoId){
    if (!_isLocalAudio(videoId)) return;
    var src = videoId;
    if (src.charAt(0) !== '/' && src.indexOf('://') === -1){
      var base = window.location.href.replace(/\/[^\/]*$/, '/');
      src = base + src;
    }
    if (!this._audio){
      this._audio = new Audio();
      var self = this;
      this._audio.addEventListener('ended', function(){
        self._state = 0;
        try { if (typeof self._events.onStateChange === 'function') self._events.onStateChange({data:0}); } catch(e){}
      });
    }
    this._audio.src = src;
    this._audio.volume = this._volume / 100;
    this._audio.muted  = this._muted;
    this._videoData.video_id = videoId;
    this._videoData.title    = videoId.split('/').pop().replace(/\.[^.]+$/, '');
    this.playerInfo.videoData = this._videoData;
  };
  AudioPlayer.prototype.loadVideoById = function(a){
    var vid = typeof a === 'object' ? (a.videoId||'') : (a||'');
    this._loadAudio(vid);
    if (this._audio && _isLocalAudio(vid)){
      var self = this;
      this._state = 1;
      // ICC Plus retries without CORS if crossOrigin fails (noCors fallback)
      var tryPlay = function(withCors){
        if(withCors) self._audio.crossOrigin = 'anonymous';
        else self._audio.removeAttribute('crossOrigin');
        var p = self._audio.play();
        if(p && typeof p.catch === 'function'){
          p.catch(function(err){
            if(withCors && (String(err).indexOf('CORS') !== -1 || String(err).indexOf('cross') !== -1)){
              // Retry without CORS
              self._audio.src = self._audio.src; // reload
              tryPlay(false);
            } else {
              self._state = -1;
              console.warn('[CYOA stub] Audio play failed:', err);
            }
          });
        }
      };
      tryPlay(true);
      try { if (typeof self._events.onStateChange === 'function') self._events.onStateChange({data:1}); } catch(e){}
    }
  };
  AudioPlayer.prototype.cueVideoById   = function(a){ this._loadAudio(typeof a==='object'?a.videoId||'':a||''); };
  AudioPlayer.prototype.playVideo      = function(){ if(this._audio){this._audio.play().catch(function(){});this._state=1;} };
  AudioPlayer.prototype.pauseVideo     = function(){ if(this._audio){this._audio.pause();this._state=2;} };
  AudioPlayer.prototype.stopVideo      = function(){ if(this._audio){this._audio.pause();this._audio.currentTime=0;this._state=0;} };
  AudioPlayer.prototype.seekTo         = function(s){ if(this._audio)this._audio.currentTime=s; };
  AudioPlayer.prototype.destroy        = function(){ if(this._audio){this._audio.pause();this._audio=null;} };
  AudioPlayer.prototype.getPlayerState = function(){ return this._state; };
  AudioPlayer.prototype.getDuration    = function(){ return this._audio?this._audio.duration||0:0; };
  AudioPlayer.prototype.getCurrentTime = function(){ return this._audio?this._audio.currentTime||0:0; };
  AudioPlayer.prototype.setVolume      = function(v){ this._volume=v; if(this._audio)this._audio.volume=v/100; };
  AudioPlayer.prototype.getVolume      = function(){ return this._volume; };
  AudioPlayer.prototype.mute           = function(){ this._muted=true; if(this._audio)this._audio.muted=true; };
  AudioPlayer.prototype.unMute         = function(){ this._muted=false; if(this._audio)this._audio.muted=false; };
  AudioPlayer.prototype.isMuted        = function(){ return this._muted; };
  AudioPlayer.prototype.setLoop        = function(l){ if(this._audio)this._audio.loop=l; };

  window.YT = window.YT || {};
  window.YT.Player      = AudioPlayer;
  window.YT.__cyoa_stub__ = true;
  window.YT.PlayerState = {UNSTARTED:-1,ENDED:0,PLAYING:1,PAUSED:2,BUFFERING:3,CUED:5};

  // Fire BOTH callback names — viewers vary:
  // Standard:    window.onYouTubeIframeAPIReady()   (documented by Google)
  // New_Viewer:  window.onYouTubeIframeAPI()        (custom callback)
  function _fireCallbacks(){
    var cbs = ['onYouTubeIframeAPIReady', 'onYouTubeIframeAPI'];
    for (var i=0; i<cbs.length; i++){
      try { if (typeof window[cbs[i]] === 'function') window[cbs[i]](); } catch(e){}
    }
  }
  // Fire once immediately (for scripts already parsed)
  setTimeout(_fireCallbacks, 0);
  // Fire again after DOMContentLoaded in case viewer waits for it
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ setTimeout(_fireCallbacks, 50); });
  } else {
    setTimeout(_fireCallbacks, 50);
  }
})();
"""
        pathlib.Path(stub_local).write_text(stub, encoding="utf-8")
        return stub_local

    def write_project_payload(self, project_url: str, project_text: str) -> None:
        root_local = os.path.join(self.output_folder, "project.json")
        pathlib.Path(root_local).write_text(project_text, encoding="utf-8")
        root_abs = os.path.abspath(root_local)
        self._source_for_local[root_abs] = project_url or self.start_url
        if project_url:
            with self._lock:
                self._downloaded[project_url] = root_local

        alias_paths: Set[str] = set()
        if project_url:
            parsed = urlparse(project_url)
            basename = os.path.basename(parsed.path)
            if basename and basename.lower() != "project.json":
                alias_paths.add(os.path.join(self.output_folder, basename))

        for alias in alias_paths:
            if os.path.abspath(alias) == root_abs:
                continue
            os.makedirs(os.path.dirname(alias), exist_ok=True)
            pathlib.Path(alias).write_text(project_text, encoding="utf-8")
            rel_alias = os.path.relpath(alias, self.output_folder).replace("\\", "/")
            self._project_aliases.append(rel_alias)
            self._source_for_local[os.path.abspath(alias)] = project_url or self.start_url
            logger.info(f"  Project alias: {rel_alias}")

    def write_manifest(self, project_url: str = "") -> str:
        def _uniq(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
            seen: Set[tuple] = set()
            out = []
            for item in items:
                key = (item.get("url"), item.get("local"), item.get("kind"), item.get("error"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
            return out

        success = _uniq(self._success_items)
        failed  = _uniq(self._failed_items)

        grouped_success: Dict[str, List[str]] = {}
        for item in success:
            grouped_success.setdefault(item.get("kind", "assets"), []).append(item.get("local", ""))

        grouped_failed: Dict[str, List[str]] = {}
        for item in failed:
            item_url = item.get("url", "")
            ext  = os.path.splitext(urlparse(item_url).path)[1].lower()
            kind = (
                "media"  if ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS else
                "images" if ext in IMAGE_EXTENSIONS else
                "fonts"  if ext in FONT_EXTENSIONS else
                "css"    if ext in STYLE_EXTENSIONS else
                "js"     if ext in SCRIPT_EXTENSIONS else
                "assets"
            )
            grouped_failed.setdefault(kind, []).append(item_url)

        report_text = format_backup_report_text(
            start_url=self.start_url,
            project_url=project_url,
            project_root="project.json",
            project_aliases=self._project_aliases,
            downloaded=success,
            failed=failed,
            downloaded_groups=grouped_success,
            failed_groups=grouped_failed,
            notes=["Engine mode: standard website", "Project payload written to project.json root."],
        )

        # Append collision log if any
        if self._collision_log:
            lines = [
                "",
                "=" * 60,
                "PATH COLLISIONS",
                "=" * 60,
                "These files had name conflicts and were renamed.",
                "The JS/CSS referencing them has been updated accordingly.",
                "",
            ]
            for entry in self._collision_log:
                lines.append(f"URL      : {entry['url']}")
                lines.append(f"Wanted   : {entry['original_path']}")
                lines.append(f"Saved as : {entry['saved_as']}")
                lines.append("")
            report_text += "\n".join(lines)

        manifest_path = os.path.join(self.output_folder, "backup_report.txt")
        pathlib.Path(manifest_path).write_text(report_text, encoding="utf-8")
        logger.info(f"  Manifest: {os.path.relpath(manifest_path, self.output_folder)}")
        if failed:
            logger.info(
                "  ICC asset failure details are included in backup_report.txt."
            )
        return manifest_path


__all__ = [
    "WebsiteDownloader", "get_headers_for_url", "is_zip_bytes", "get_source",
    "url_file_exists", "_directory_base_url", "get_first_folder_from_url",
    "get_first_subdomain", "strip_document_from_url",
]
