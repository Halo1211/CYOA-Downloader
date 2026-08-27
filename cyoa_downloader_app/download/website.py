"""Website mirroring domain implementation.

Phase 37 moved ``WebsiteDownloader`` out of ``legacy.py``. The class body is
kept mechanically equivalent; high-risk collaborators still resolve through
small runtime proxies so global legacy state and patch ordering remain intact.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import pathlib
import re
import threading
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests

from ..config.settings import _load_settings
from ..constants.assets import (
    _YOUTUBE_URL_RE,
    AUDIO_EXTENSIONS,
    FONT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SCRIPT_EXTENSIONS,
    STYLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from ..core.atomic_io import atomic_write_text
from ..core.cancellation import _emit_progress_event, _raise_if_cancelled
from ..core.paths import _safe_join
from ..core.progress import DownloadCancelledError
from ..core.url_utils import _directory_base_url, canonicalize_url
from ..diagnostics.reports import format_backup_report_text
from ..integrations.ai_core import (
    AIUsageBudget,
    _get_ai_provider,
    _normalize_ai_mode,
    _normalize_ai_provider,
    _ssrf_block_cross_origin,
)
from ..logging_setup import logger
from ..network.browser import BrowserFetchSession
from ..network.runtime_capture import _is_archive_noise_url
from ..project.discover import (
    get_first_folder_from_url,
    get_source,
    strip_document_from_url,
    url_file_exists,
)
from ..project.parse import is_zip_bytes
from ._bridge import legacy
from .asset_scan import _infer_dynamic_asset_paths, _safe_response_text
from .headers import get_headers_for_url
from .package import (
    atomic_stream_response_to_file,
    clean_url_path_component,
    get_first_subdomain,
)

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


_MAX_INLINE_DOCUMENT_B64_CHARS = 64 * 1024 * 1024
_ATOB_VARIABLE_RE = re.compile(
    r"\batob\s*\(\s*(?P<name>[A-Za-z_$][\w$]*)\s*\)",
    re.IGNORECASE,
)


def _decode_inline_document_payload(html_text: str) -> str:
    """Unwrap a static Base64 HTML document written by a bootstrap script.

    Some single-file sites hide their complete document in a JavaScript string,
    decode it with ``atob()``, and replace the current page via
    ``document.write()``. Static asset scanners otherwise see only the tiny
    bootstrap while runtime capture sees only resources reached by the current
    interaction state.

    This deliberately does not execute JavaScript. It accepts only a Base64
    literal assigned to the exact identifier passed to ``atob()``, requires a
    document-writing bootstrap, bounds the payload size, and validates that the
    decoded UTF-8 value is a complete HTML document.
    """
    if not html_text or "atob" not in html_text or "document.write" not in html_text:
        return html_text

    variable_names = list(dict.fromkeys(
        match.group("name") for match in _ATOB_VARIABLE_RE.finditer(html_text)
    ))
    for variable_name in variable_names:
        assignment_re = re.compile(
            rf"\b(?:var|let|const)\s+{re.escape(variable_name)}\s*=\s*"
            r"(?P<quote>[\"'])(?P<data>[A-Za-z0-9+/=\r\n\t ]+)(?P=quote)\s*;",
            re.IGNORECASE,
        )
        match = assignment_re.search(html_text)
        if match is None:
            continue
        encoded = "".join(match.group("data").split())
        if not encoded or len(encoded) > _MAX_INLINE_DOCUMENT_B64_CHARS:
            if encoded:
                logger.warning(
                    "Inline HTML payload is too large to decode safely: %d Base64 characters",
                    len(encoded),
                )
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8-sig")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue

        probe = decoded.lstrip()[:2048].lower()
        if not probe.startswith(("<!doctype html", "<html")):
            continue
        if "</html>" not in decoded.lower():
            continue
        logger.info(
            "  Decoded inline Base64 HTML bootstrap: %d -> %d characters",
            len(encoded),
            len(decoded),
        )
        return decoded

    return html_text

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
        # Only rewrite real string delimiters. Framework payloads such as
        # Next.js React Flight embed JSON inside a JavaScript string and use
        # escaped delimiters (\"/_next/...js\"). Treating those escaped quotes
        # as delimiters removes their backslashes and makes the whole inline
        # script invalid JavaScript.
        r'(?<!\\)(?P<quote>["\'])(?P<url>(?:https?:)?//[^"\']+|(?:\./|\.\./|/)?[^"\']+\.(?:json|txt|zip|js|mjs|css|png|jpe?g|gif|webp|avif|bmp|svg|ico|mp3|ogg|wav|m4a|aac|opus|woff2?|ttf|otf|eot)(?:\?[^"\']*)?)(?<!\\)(?P=quote)',
        re.IGNORECASE,
    )
    _css_url_re = re.compile(r'url\(([^)]+)\)', re.IGNORECASE)
    _css_import_re = re.compile(
        r'@import\s+(?:url\()?["\']?([^"\')\s]+)["\']?\)?',
        re.IGNORECASE,
    )
    _css_comment_re = re.compile(r'/\*.*?\*/', re.DOTALL)
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
        self.start_url     = canonicalize_url(start_url)
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
        self.base_url = _directory_base_url(self.start_url)
        self.max_workers = max_workers
        self.session = create_retry_session()
        self._lock = threading.Lock()
        self._downloaded: Dict[str, str] = {}
        self._source_for_local: Dict[str, str] = {}
        self._used_local_paths: Set[str] = set()
        parsed = urlparse(self.start_url)
        self.base_origin = f"{parsed.scheme}://{parsed.netloc}"
        self.start_html_local = _safe_join(self.output_folder, "index.html")
        self._success_items: List[Dict[str, str]] = []
        self._failed_items: List[Dict[str, str]] = []
        self._project_aliases: List[str] = []
        self._collision_log: List[Dict[str, str]] = []
        self._custom_viewer_route = False
        self._browser_fetch_session = None
        self._browser_transport_preferred = False

    def close(self) -> None:
        """Release an optional reusable browser transport."""
        session = self._browser_fetch_session
        self._browser_fetch_session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def __del__(self) -> None:  # pragma: no cover - best-effort process cleanup
        try:
            self.close()
        except Exception:
            pass

    def _fetch_with_browser(self, url: str) -> Optional[requests.Response]:
        """Return a requests-compatible response from a shared browser page."""
        if self.archive_strategy not in {"auto", "browser"}:
            return None
        try:
            parsed = urlparse(url)
            start = urlparse(self.start_url)
            if (parsed.scheme.lower(), parsed.netloc.lower()) != (
                start.scheme.lower(), start.netloc.lower(),
            ):
                return None
            if self._browser_fetch_session is None:
                self._browser_fetch_session = BrowserFetchSession()
            fetched = self._browser_fetch_session.fetch(url)
            if fetched is None:
                return None
            response = requests.Response()
            response.status_code = fetched.status
            response.url = fetched.url
            response.headers.update(fetched.headers)
            response._content = fetched.content
            # This response is synthesized from an already-buffered browser
            # body and has no urllib3 ``raw`` stream. Mark it consumed so
            # requests.iter_content() slices ``_content`` instead of trying to
            # call ``response.raw.read``.
            response._content_consumed = True
            # Browser response bodies preserve the server bytes. Do not run
            # statistical encoding detection here: short/minified documents
            # containing mostly ASCII can be misclassified as a Windows code
            # page, corrupting UTF-8 punctuation and emoji inside React Flight
            # payloads. That produces valid JavaScript with different data and
            # silently prevents framework hydration.
            declared_encoding = requests.utils.get_encoding_from_headers(response.headers)
            if str(declared_encoding or "").lower() in {
                "", "iso-8859-1", "iso8859-1", "latin-1", "latin1",
            }:
                declared_encoding = "utf-8"
            response.encoding = declared_encoding
            self._browser_transport_preferred = True
            logger.info("  [Browser transport] %s -> %d bytes", url, len(fetched.content))
            return response
        except DownloadCancelledError:
            raise
        except Exception as exc:
            logger.debug("Browser transport unavailable for %s: %s", url, exc)
            return None

    def download(self) -> None:
        os.makedirs(self.output_folder, exist_ok=True)
        logger.info(f"ICC download started: {self.start_url}")
        self._download_html(self.start_url, self.start_html_local)
        logger.info(f"ICC package saved: {self.output_folder}/")

        # Auto profiling depends on the localized entry HTML and its directly
        # linked bundles, so resolve it here before deciding whether deep scan
        # is redundant. Previously Auto skipped deep scan here and was only
        # profiled later by run_archive_extensions(); targets that ultimately
        # selected Classic therefore received neither deep scan nor runtime
        # capture.
        auto_profile = getattr(self, "archive_auto_profile", None)
        if self.archive_strategy == "auto" and auto_profile is None:
            try:
                from .archive_profiler import profile_archive_target

                auto_profile = profile_archive_target(self)
                self.archive_auto_profile = auto_profile
            except DownloadCancelledError:
                raise
            except Exception as exc:
                # Profiling is an optimization. Falling back to deep scan is
                # safer than silently reducing archive coverage.
                logger.warning(
                    "Auto archive profiling failed; keeping generic deep scan enabled: %s",
                    exc,
                )

        # Deep scan: find assets referenced in JS/CSS bundles not in HTML
        if (
            self.archive_strategy == "auto"
            and _auto_profile_uses_project_data(auto_profile)
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
            max_workers=self.max_workers,
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

        directory_entries: Dict[str, Set[str]] = {}

        def _exists_with_exact_case(candidate: str) -> bool:
            """Check portable path casing even on case-insensitive Windows."""
            root_abs = os.path.abspath(self.output_folder)
            candidate_abs = os.path.abspath(candidate)
            try:
                if os.path.commonpath([root_abs, candidate_abs]) != root_abs:
                    return False
                relative = os.path.relpath(candidate_abs, root_abs)
            except (OSError, ValueError):
                return False
            current = root_abs
            if relative == ".":
                return os.path.exists(current)
            for part in pathlib.Path(relative).parts:
                entries = directory_entries.get(current)
                if entries is None:
                    try:
                        entries = set(os.listdir(current))
                    except OSError:
                        return False
                    directory_entries[current] = entries
                if part not in entries:
                    return False
                current = os.path.join(current, part)
            return os.path.exists(current)

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
                css_text = self._css_comment_re.sub("", css_text)
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
                    optional_font_fallbacks: Set[str] = set()

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
                            css = self._css_comment_re.sub(
                                "", style_tag.get_text(" ", strip=False)
                            )
                            for match in self._css_url_re.finditer(css):
                                _record(refs, match.group(1))
                            for match in self._css_import_re.finditer(css):
                                _record(refs, match.group(1))

                    elif ext == ".css":
                        text = self._css_comment_re.sub("", text)
                        # A single @font-face commonly lists EOT, WOFF2, WOFF,
                        # and TTF alternatives. Modern deployments may publish
                        # only WOFF2 while retaining legacy fallback URLs in
                        # generated CSS. If at least one source in the same
                        # declaration exists, absent alternatives are not a
                        # broken runtime dependency.
                        for font_face in re.finditer(
                            r"@font-face\s*\{(?P<body>[^{}]*)\}",
                            text,
                            flags=re.IGNORECASE | re.DOTALL,
                        ):
                            font_refs = [
                                match.group(1).strip().strip("'\"")
                                for match in self._css_url_re.finditer(font_face.group("body"))
                            ]
                            local_font_refs = [ref for ref in font_refs if _is_local(ref)]
                            if any(
                                _exists_with_exact_case(_local_candidate(local_path, ref))
                                for ref in local_font_refs
                            ):
                                optional_font_fallbacks.update(
                                    ref for ref in local_font_refs
                                    if not _exists_with_exact_case(_local_candidate(local_path, ref))
                                )
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
                        if any(_exists_with_exact_case(path) for path in (
                            abs_ref, decoded_abs_ref, root_abs_ref, decoded_root_abs_ref,
                            public_root_abs_ref, decoded_public_root_abs_ref,
                        )):
                            ok_refs.add(label)
                        elif ref in optional_font_fallbacks:
                            logger.debug(
                                "Optional @font-face fallback absent but another "
                                "declared format exists: %s → %s",
                                source_rel,
                                ref,
                            )
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
        """Second-pass scan for downloaded text and JSON assets."""
        for root, _, files in os.walk(self.output_folder):
            for name in files:
                if name in {
                    "archive_manifest.json", "download_state.json",
                    "download_history.json", "cyoa_manifest.json",
                }:
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".html", ".css", ".js", ".mjs", ".json"}:
                    continue
                local_path = os.path.join(root, name)
                source_url = self._source_for_local.get(os.path.abspath(local_path), self.start_url)
                try:
                    text = pathlib.Path(local_path).read_text(encoding="utf-8", errors="ignore")
                    if ext == ".css":
                        updated = self._process_css(text, source_url, local_path)
                    elif ext in {".js", ".mjs"}:
                        updated = self._process_js(text, source_url, local_path)
                    elif ext == ".json":
                        updated = self._rewrite_known_downloaded_urls(text, source_url, local_path)
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
            if self._browser_transport_preferred:
                r = self._fetch_with_browser(url)
                if r is None:
                    self._failed_items.append({"url": url, "error": "browser transport failed"})
                    return None
            else:
                r = fetch_response(url, extra_headers=headers, timeout=20, as_bytes=False, stream=True)
                if r is None:
                    r = self._fetch_with_browser(url)
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
        # Hand-authored ICC exports sometimes use ``https:/host``. Treat it
        # as the same absolute URL before urljoin() can turn it into a path
        # under the viewer origin.
        url = re.sub(r"^(https?):/(?!/)", r"\1://", url, flags=re.IGNORECASE)
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
            return _safe_join(self.output_folder, "project.json")

        # ── Preserve original relative path from site root ────────────────
        parsed       = urlparse(url)
        start_parsed = urlparse(self.start_url)
        if parsed.netloc == start_parsed.netloc:
            # ``parsed.path`` may retain percent-encoding while asset paths
            # below are decoded with ``unquote``.  Compare like with like;
            # otherwise routes containing spaces, apostrophes, or other
            # encoded characters miss this branch and are incorrectly saved
            # from the domain root (for example CYOA's/Fate NSFWCYOA/v1.5/...).
            start_dir  = unquote(start_parsed.path.rstrip("/") + "/")
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
                forced_ext = {
                    "css": ".css",
                    "js": ".js",
                }.get(kind, "")
                if forced_ext and rel_ext.lower() not in (
                    STYLE_EXTENSIONS if kind == "css" else SCRIPT_EXTENSIONS
                ):
                    # A script endpoint can legitimately end in .html (the
                    # archived vue-select proxy on cyoa.cafe does). Saving it
                    # with that suffix makes later passes parse JavaScript as
                    # HTML and escape && / => into &amp;&amp; / =&gt;.
                    rel_parts = rel_root + forced_ext
                elif not rel_ext:
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
        filename_root, filename_ext = os.path.splitext(filename)
        if kind == "js" and filename_ext.lower() not in SCRIPT_EXTENSIONS:
            filename = filename_root + ".js"
        elif kind == "css" and filename_ext.lower() not in STYLE_EXTENSIONS:
            filename = filename_root + ".css"
        normalized_query = urlparse(self._normalize_cache_key(url)).query
        if normalized_query:
            root, ext = os.path.splitext(filename)
            digest = hashlib.sha1(normalized_query.encode("utf-8", "replace")).hexdigest()[:10]
            filename = f"{root}_{digest}{ext}"
        folder_name = kind if kind not in {"html", "json"} else "assets"
        folder = _safe_join(self.output_folder, folder_name, fallback="assets")
        os.makedirs(folder, exist_ok=True)

        local = _safe_join(folder, filename, fallback=kind or "asset")
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

    def _local_asset_reference(self, from_file: str, to_file: str) -> str:
        """Return a browser-safe local reference for a downloaded asset.

        Next.js client chunks pass image strings back through ``next/image``,
        which accepts absolute URLs or paths beginning with ``/`` but rejects
        ordinary filesystem-relative values such as ``../../../images/x.png``.
        A Next.js archive already needs an HTTP server for its root-relative
        runtime chunks, so keep rewritten bundle assets root-relative too.
        """
        relative = self._rel(from_file, to_file)
        try:
            owner = os.path.relpath(from_file, self.output_folder).replace("\\", "/")
            target = os.path.relpath(to_file, self.output_folder).replace("\\", "/")
            if (
                owner.lower().startswith("_next/static/")
                and target not in {"", "."}
                and not target.startswith(("../", "/"))
            ):
                return "/" + target
        except (OSError, ValueError):
            pass
        return relative

    def _download_asset(self, url: str, preferred_kind: str = "", referrer_url: Optional[str] = None) -> Optional[str]:
        _raise_if_cancelled()
        full = self._normalize_remote_url(url, referrer_url)
        if not full:
            return None
        # Keep the originally resolved URL even when one of the recovery
        # branches below succeeds from a different location.  The original
        # cache entry is installed as an in-progress sentinel; leaving that
        # sentinel behind makes every later reference to the same asset look
        # like a permanent failure despite the successful fallback download.
        requested_full = full
        requested_cache_key = self._normalize_cache_key(full)

        # Analytics, feedback widgets, and telemetry are not part of an
        # offline story. Their bootstrap scripts recursively reference
        # generated endpoints and can dominate a mirror with retries/404s.
        host = (urlparse(full).hostname or "").lower()
        if (
            _is_archive_noise_url(full)
            or host in self._telemetry_hosts
            or host.endswith((".sentry.io", ".posthog.com"))
        ):
            logger.debug("  Telemetry skipped: %s", full)
            return None

        # SSRF screen on the deep-scan asset chokepoint.
        # A scanned JS/CSS/HTML file from an untrusted site can reference a
        # cross-origin internal host; block it unless same-origin as the page
        # being mirrored (self.start_url) or --allow-internal-hosts is set.
        if _ssrf_block_cross_origin(full, getattr(self, "start_url", "")):
            error = "blocked: cross-origin internal host"
            logger.warning(f"  [SSRF blocked] cross-origin internal host: {full}")
            with self._lock:
                self._downloaded[full] = None
                self._failed_items.append({"url": full, "error": error})
            _emit_progress_event(
                "file_failed",
                name=os.path.basename(urlparse(full).path) or full,
                url=full,
                error=error,
            )
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
        if r is None and referrer_url:
            ref_path = urlparse(referrer_url).path.lower()
            relative_input = not url.startswith(("http", "//", "data:", "#"))
            css_root_candidate = False
            if ref_path.endswith(".css"):
                candidate = urlparse(full)
                start = urlparse(self.start_url)
                base_path = start.path.rstrip("/")
                candidate_path = candidate.path
                relative_candidate = (
                    candidate.scheme.lower() == start.scheme.lower()
                    and candidate.netloc.lower() == start.netloc.lower()
                    and bool(base_path)
                    and candidate_path.startswith(base_path + "/")
                )
                if relative_candidate:
                    css_root_candidate = "/" in candidate_path[len(base_path) + 1:]
            if relative_input or css_root_candidate:
                # Custom viewers commonly put data paths in a JS bundle but
                # intend them relative to the viewer route, not the domain
                # root.  Preserve the normal root-relative fallback for
                # ordinary sites.
                js_root = self.base_url if self._custom_viewer_route else self.start_url
                if css_root_candidate:
                    parsed_full = urlparse(full)
                    base_path = urlparse(self.start_url).path.rstrip("/")
                    filename = parsed_full.path.rsplit("/", 1)[-1]
                    alt = urlunparse(parsed_full._replace(
                        path=base_path + "/" + filename
                    ))
                else:
                    alt = self._normalize_remote_url(url, js_root)
                if alt and alt != full:
                    # Check cache first — same raw string may appear multiple times in data.js
                    with self._lock:
                        if alt in self._downloaded:
                            cached = self._downloaded[alt]
                            if cached:
                                self._downloaded[requested_full] = cached
                                self._downloaded[requested_cache_key] = cached
                            return cached
                    r_alt = self._fetch(alt)
                    _raise_if_cancelled()
                    if r_alt:
                        logger.info(f"  root-fallback: {url} → {alt}")
                        self._failed_items = [
                            item for item in self._failed_items
                            if item.get("url") != full
                        ]
                        r    = r_alt
                        full = alt

        # Some Vite builds are deployed below a subfolder but retain absolute
        # ``/assets/...`` references from the original root deployment. The
        # root URL is a genuine 404, while ``/<viewer-route>/assets/...`` is
        # valid. Try this route-relative variant only after the root request
        # failed and only for same-origin /assets paths.
        if r is None:
            parsed_full = urlparse(full)
            parsed_start = urlparse(self.start_url)
            route_prefix = parsed_start.path.rstrip("/")
            same_origin = (
                parsed_full.scheme.lower() == parsed_start.scheme.lower()
                and parsed_full.netloc.lower() == parsed_start.netloc.lower()
            )
            if (
                same_origin
                and route_prefix
                and parsed_full.path.startswith("/assets/")
                and not parsed_full.path.startswith(route_prefix + "/")
            ):
                alt = urlunparse(parsed_full._replace(
                    path=route_prefix + parsed_full.path
                ))
                if alt != full:
                    with self._lock:
                        if alt in self._downloaded:
                            cached = self._downloaded[alt]
                            if cached:
                                self._downloaded[requested_full] = cached
                                self._downloaded[requested_cache_key] = cached
                            return cached
                    r_alt = self._fetch(alt)
                    _raise_if_cancelled()
                    if r_alt:
                        logger.info(f"  route-fallback: {url} → {alt}")
                        self._failed_items = [
                            item for item in self._failed_items
                            if item.get("url") != full
                        ]
                        r = r_alt
                        full = alt

        if not r:
            failure = next(
                (item for item in reversed(self._failed_items) if item.get("url") == full),
                None,
            )
            if failure is None:
                failure = {"url": full, "error": "request failed"}
                self._failed_items.append(failure)
            _emit_progress_event(
                "file_failed",
                name=os.path.basename(urlparse(full).path) or full,
                url=full,
                error=str(failure.get("error") or "request failed"),
            )
            return None

        content_type = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        effective_kind = self._kind_from(
            full, content_type=content_type, preferred_kind=preferred_kind,
        )
        requested_ext = os.path.splitext(urlparse(full).path.lower())[1]
        if content_type.startswith("text/html") and requested_ext in (IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | FONT_EXTENSIONS):
            error = f"Content-Type mismatch for binary asset: {content_type or 'unknown'}"
            logger.warning(f"  {error}: {full}")
            self._failed_items.append({"url": full, "error": error})
            _emit_progress_event(
                "file_failed",
                name=os.path.basename(urlparse(full).path) or full,
                url=full,
                error=error,
            )
            try:
                r.close()
            except Exception as exc:
                logger.debug(f"Response close failed for rejected asset {full}: {exc}")
            return None
        if effective_kind in {"js", "css"} and content_type in {
            "text/html", "application/xhtml+xml",
        }:
            prefix = bytes(r.content or b"")[:512].lstrip().lower()
            if prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
                error = f"Content-Type mismatch for {effective_kind} asset: HTML document"
                logger.warning("  %s: %s", error, full)
                self._failed_items.append({"url": full, "error": error})
                try:
                    r.close()
                except Exception as exc:
                    logger.debug("Response close failed for rejected asset %s: %s", full, exc)
                return None
        local = self._allocate_local_path(full, content_type=content_type, preferred_kind=preferred_kind)
        abs_local = os.path.abspath(local)
        os.makedirs(os.path.dirname(local), exist_ok=True)

        try:
            if effective_kind == "css":
                raw_text = _safe_response_text(r)
                _raise_if_cancelled()
                _throttle_bandwidth(len(r.content))
                content = self._process_css(raw_text, full, local)
                atomic_write_text(local, content)
            elif effective_kind == "js":
                raw_text = _safe_response_text(r)
                _raise_if_cancelled()
                _throttle_bandwidth(len(r.content))
                content = self._process_js(raw_text, full, local)
                atomic_write_text(local, content)
            elif effective_kind == "html":
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
            for alias in {ck, requested_full, requested_cache_key}:
                self._downloaded[alias] = local
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
        self._download_runtime_template_assets(text, referrer_url)
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
            # A custom viewer may construct a real asset URL from a template
            # string, e.g. ``"${i.id}.jpg"``.  The placeholder is not a
            # fetchable filename; downloading it produces a noisy 404 for
            # ``$%7Bi.id%7D.jpg`` and can make a valid custom viewer look
            # broken.  Leave the runtime expression for the browser.
            if "${" in original:
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
            rel = self._local_asset_reference(local_text_path, local)
            return f'{m.group("quote")}{rel}{m.group("quote")}'
        rewritten = self._quoted_asset_re.sub(repl, text)

        # Project JSON and rich JS strings may contain ``label https://...``
        # rather than a URL occupying the whole quoted value. Localize only
        # absolute URLs that are already present in the successful download
        # cache; unavailable/runtime URLs stay online for diagnostics.
        embedded_url_re = re.compile(
            r"(?P<url>https?:/{1,2}[^\s\"'<>]+)", re.IGNORECASE
        )

        def rewrite_embedded(match: re.Match) -> str:
            original = match.group("url")
            normalized = self._normalize_remote_url(original, referrer_url)
            if not normalized:
                return original
            local = self._downloaded.get(normalized)
            if local is None:
                local = self._downloaded.get(self._normalize_cache_key(normalized))
            if not local or not os.path.isfile(local):
                return original
            return self._local_asset_reference(local_text_path, local)

        return embedded_url_re.sub(rewrite_embedded, rewritten)

    def _rewrite_known_downloaded_urls(
        self,
        text: str,
        referrer_url: str,
        local_text_path: str,
    ) -> str:
        """Rewrite absolute URL tokens already saved by the archive pass.

        React/Webpack bundles can contain the complete project data as a
        JavaScript string. Rewriting every string is unsafe, but leaving the
        bundle untouched leaves successfully downloaded CDN images online.
        This narrow pass changes only absolute URLs present in ``_downloaded``;
        failed or runtime-generated URLs remain unchanged.
        """
        if not text:
            return text

        def repl(match: re.Match) -> str:
            original = match.group("url")
            normalized = original.replace("\\/", "/")
            if not re.match(r"^(?:https?:/{1,2}|//)", normalized, re.IGNORECASE):
                return match.group(0)
            full = self._normalize_remote_url(normalized, referrer_url)
            if not full:
                return match.group(0)
            local = self._downloaded.get(full)
            if local is None:
                local = self._downloaded.get(self._normalize_cache_key(full))
            if not local or not os.path.isfile(local):
                return match.group(0)
            rel = self._local_asset_reference(local_text_path, local)
            return f'{match.group("quote")}{rel}{match.group("quote")}'

        rewritten = self._quoted_asset_re.sub(repl, text)

        # Rich project records can store ``label https://cdn/file.gif`` in a
        # single JSON/JS string. Rewrite only URLs known to be downloaded.
        embedded_url_re = re.compile(
            r"(?P<url>https?:/{1,2}[^\s\"'<>]+)", re.IGNORECASE
        )

        def rewrite_embedded(match: re.Match) -> str:
            original = match.group("url")
            full = self._normalize_remote_url(original, referrer_url)
            if not full:
                return original
            local = self._downloaded.get(full)
            if local is None:
                local = self._downloaded.get(self._normalize_cache_key(full))
            if not local or not os.path.isfile(local):
                return original
            return self._local_asset_reference(local_text_path, local)

        return embedded_url_re.sub(rewrite_embedded, rewritten)

    def _download_runtime_template_assets(self, text: str, referrer_url: str) -> None:
        """Download concrete assets exposed by simple JS templates.

        A custom viewer may keep records in any array/object shape and build a
        URL from a property, for example ``${item.slug}.webp`` or
        ``images/${entry.file}.png``.  The old implementation special-cased
        one field name and one variable name, which made the downloader look
        like it belonged to a single CYOA.  This deliberately conservative
        extractor handles only a simple ``object.property`` placeholder and
        leaves more complex expressions for the browser/runtime archive.
        """
        if not text:
            return

        # Some viewers select an asset from a numeric range at runtime, for
        # example ``Math.floor(Math.random() * 135) + 1`` followed by
        # ``"comics/" + randomIndex + ".png"``.  The browser only requests
        # one random item, so a normal static scan sees no concrete URL and
        # the offline copy gets a broken image on most loads.  Prefetch the
        # bounded range when the path expression is simple enough to prove.
        # This is intentionally capped: the downloader must not turn an
        # arbitrary runtime expression into an unbounded crawl.
        range_re = re.compile(
            r"\b(?P<var>[A-Za-z_$][\w$]*)\s*=\s*"
            r"Math\.floor\s*\(\s*Math\.random\s*\(\s*\)\s*\*\s*"
            r"(?P<count>\d+)\s*\)\s*"
            r"(?:\+\s*(?P<start>\d+))?",
            re.IGNORECASE,
        )
        path_re = re.compile(
            r"(?P<q1>['\"])(?P<prefix>[A-Za-z0-9_./-]{0,160})(?P=q1)"
            r"\s*\+\s*(?P<var>[A-Za-z_$][\w$]*)\s*\+\s*"
            r"(?P<q2>['\"])(?P<suffix>[A-Za-z0-9_./?=&%:+-]{0,160}"
            r"\.[A-Za-z0-9]{1,8}(?:\?[^'\"\s<>]*)?)(?P=q2)",
            re.IGNORECASE,
        )
        max_runtime_range = 1000
        seen_numeric = set()
        ranges = {}
        for match in range_re.finditer(text):
            count = int(match.group("count"))
            if count <= 0 or count > max_runtime_range:
                continue
            start = int(match.group("start") or 0)
            ranges[match.group("var")] = (start, start + count - 1)

        for path_match in path_re.finditer(text):
            bounds = ranges.get(path_match.group("var"))
            if not bounds:
                continue
            prefix = path_match.group("prefix")
            suffix = path_match.group("suffix")
            for number in range(bounds[0], bounds[1] + 1):
                raw_path = f"{prefix}{number}{suffix}"
                asset_url = urljoin(referrer_url, raw_path)
                if asset_url in seen_numeric:
                    continue
                seen_numeric.add(asset_url)
                self._download_asset(
                    asset_url,
                    preferred_kind=self._asset_kind_from_path(asset_url),
                    referrer_url=referrer_url,
                )

        template_re = re.compile(
            r"(?P<prefix>[A-Za-z0-9_./-]{0,160})"
            r"\$\{\s*(?P<root>[A-Za-z_$][\w$]*)\s*\.\s*"
            r"(?P<prop>[A-Za-z_$][\w$]*)\s*\}"
            r"(?P<suffix>[A-Za-z0-9_./?=&%:+-]{0,160}\.(?P<ext>[A-Za-z0-9]{1,8})"
            r"(?:\?[^'\"`\s<>]*)?)",
            re.IGNORECASE,
        )
        templates = list(template_re.finditer(text))
        if not templates:
            return

        for template in templates:
            prop = template.group("prop")
            prefix = template.group("prefix")
            suffix = template.group("suffix")
            if not prop or not suffix:
                continue

            # Support both quoted and unquoted object keys.  Values are kept
            # bounded and conservative because this is a best-effort prefetch,
            # not a JavaScript interpreter.
            property_re = re.compile(
                rf"(?:\b{re.escape(prop)}\b|['\"]{re.escape(prop)}['\"])"
                r"\s*:\s*['\"]([^'\"\r\n]+)['\"]",
                re.IGNORECASE,
            )
            values = []
            for match in property_re.finditer(text):
                value = match.group(1).strip()
                if (
                    value
                    and len(value) <= 200
                    and not value.startswith(("data:", "javascript:"))
                    and "${" not in value
                    and value not in values
                ):
                    values.append(value)
                if len(values) >= 200:
                    break

            for value in values:
                raw_path = f"{prefix}{value}{suffix}"
                asset_url = urljoin(referrer_url, raw_path)
                self._download_asset(
                    asset_url,
                    preferred_kind=self._asset_kind_from_path(asset_url),
                    referrer_url=referrer_url,
                )

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

        def rewrite_code(code: str) -> str:
            code = self._css_import_re.sub(repl_import, code)
            code = self._css_url_re.sub(repl_url, code)
            return self._rewrite_direct_urls(code, css_url, css_local)

        # Comments often discuss CSS syntax (for example ``@import is``).
        # Treating that prose as executable CSS caused bogus requests such as
        # ``/is`` and matching false integrity failures. Preserve comments
        # byte-for-byte and rewrite only executable CSS segments.
        pieces: List[str] = []
        cursor = 0
        for comment in self._css_comment_re.finditer(css):
            pieces.append(rewrite_code(css[cursor:comment.start()]))
            pieces.append(comment.group(0))
            cursor = comment.end()
        pieces.append(rewrite_code(css[cursor:]))
        return "".join(pieces)

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
            localized = self._rewrite_known_downloaded_urls(js, js_url, js_local)
            if localized != js:
                logger.info(f"  Localized downloaded URLs in app bundle: {js_url.split('/')[-1]}")
            else:
                logger.debug(f"  No downloaded URLs to localize in app bundle: {js_url.split('/')[-1]}")
            return localized

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

    def _set_attr_local(self, tag, attr: str, page_url: str, local_html: str, preferred_kind: str = "") -> bool:
        value = tag.get(attr)
        if not value:
            return False
        if attr in {"srcset", "imagesrcset", "data-srcset"}:
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
            return bool(parts)

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
            # Subresource Integrity hashes describe the original response
            # bytes. CSS and JavaScript are intentionally rewritten during
            # localization, so retaining the remote hash can make the browser
            # reject an otherwise-valid local asset.
            if tag.has_attr("integrity"):
                del tag["integrity"]
            return True
        else:
            # The failure is recorded in backup_report/manifest. Keeping an
            # absolute URL here accurately marks it as an unresolved external
            # dependency and avoids a misleading broken local reference.
            remote = self._normalize_remote_url(value, page_url)
            if remote:
                tag[attr] = remote
            return False

    def _patch_local_audio_scripts(self) -> None:
        """Teach custom YouTube button scripts to play patched local audio."""
        marker = "__cyoaLocalAudioPatch"
        patch = r'''
;(function(){
  if (window.__cyoaLocalAudioPatch) return;
  var original = window.playVideo;
  if (typeof original !== 'function') return;
  var audio = null, current = '';
  function isLocal(value) {
    return typeof value === 'string' &&
      /^(?:audio|media|music|sounds|bgm)\//i.test(value) &&
      /\.(?:mp3|m4a|ogg|wav|aac|opus|weba)(?:[?#].*)?$/i.test(value);
  }
  window.playVideo = function(videoId) {
    if (!isLocal(videoId)) return original.apply(this, arguments);
    if (!audio) {
      audio = document.createElement('audio');
      audio.loop = true;
      audio.style.display = 'none';
      document.body.appendChild(audio);
    }
    if (current === videoId && !audio.paused) {
      audio.pause();
      return;
    }
    if (current !== videoId) {
      audio.src = videoId;
      current = videoId;
    }
    var iframe = document.getElementById('youtube-video');
    if (iframe) iframe.src = 'about:blank';
    var pending = audio.play();
    if (pending && pending.catch) pending.catch(function(){});
  };
  window.__cyoaLocalAudioPatch = true;
})();
'''
        for local in set(self._downloaded.values()):
            if not local or not str(local).lower().endswith((".js", ".mjs")):
                continue
            try:
                path = pathlib.Path(local)
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                if marker in text or "function playVideo" not in text:
                    continue
                if "youtube.com/embed" not in text:
                    continue
                path.write_text(text + patch, encoding="utf-8")
                logger.info("  Patched custom YouTube player for local audio: %s", path)
            except (OSError, UnicodeError) as exc:
                logger.debug("Could not patch local audio player %s: %s", local, exc)

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

        html_text = _decode_inline_document_payload(html_text)

        soup = BeautifulSoup(html_text, "html.parser")
        _raise_if_cancelled()
        os.makedirs(os.path.dirname(local_html), exist_ok=True)

        # Hosting providers append analytics/challenge bootstraps to otherwise
        # valid source HTML. They are not part of the CYOA and become actively
        # harmful offline (hidden iframe creation, localhost POST attempts,
        # noisy console errors). Remove only provider-identifiable scripts;
        # application scripts remain untouched.
        removed_host_scripts = 0
        for script_tag in list(soup.find_all("script")):
            src = str(script_tag.get("src") or "").strip()
            resolved_src = self._normalize_remote_url(src, url) if src else ""
            inline = script_tag.string or script_tag.get_text() or ""
            is_cloudflare_inline = (
                "__CF$cv$params" in inline
                and "challenge-platform" in inline
                and "createElement('iframe')" in inline
            )
            if (
                script_tag.has_attr("data-cf-beacon")
                or (resolved_src and _is_archive_noise_url(resolved_src))
                or is_cloudflare_inline
            ):
                script_tag.decompose()
                removed_host_scripts += 1
        if removed_host_scripts:
            logger.info(
                "  Removed %d hosting telemetry/challenge script(s) from archived HTML",
                removed_host_scripts,
            )

        # HTML's <base href> changes how every relative URL is resolved.  The
        # mirror writes localized references relative to the output file, so
        # retaining a remote base would make otherwise-correct local paths
        # point back under the live site's base path when opened offline.
        # Resolve downloads against it first, then remove it from the archive.
        html_base_url = url
        has_html_base = False
        base_tag = soup.find("base", href=True)
        if base_tag is not None:
            candidate = self._normalize_remote_url(str(base_tag.get("href") or ""), url)
            if candidate:
                html_base_url = candidate
                has_html_base = True
                logger.info("  HTML base URL detected: %s", html_base_url)
            base_tag.decompose()

        # Some hand-written CYOA viewers are served from an extensionless
        # route (for example ``/drukhari``) but store all relative assets
        # below that route (``/drukhari/js/...``).  ``urljoin`` quite
        # correctly treats the route as a document and would otherwise
        # resolve those assets at the domain root.  Keep the fetched page
        # URL intact, but use the directory route as the asset referrer for
        # this custom-viewer shape.
        html_lower = str(html_text or "").lower()
        asset_page_url = html_base_url
        if (
            url == self.start_url
            and not url.rstrip().endswith("/")
            and (
                ('id="cyoa-container"' in html_lower and "game_data" in html_lower)
                or ('id="bg-music"' in html_lower and "point-bar" in html_lower)
            )
        ):
            self._custom_viewer_route = True
            if not has_html_base:
                asset_page_url = self.base_url
            logger.info("  Custom viewer route detected; resolving entry assets below %s", asset_page_url)

        # Inline scripts are not passed through _rewrite_direct_urls.  Run
        # the conservative runtime prefetch here as well so HTML-only viewers
        # with dynamically numbered assets are complete in offline mode.
        self._download_runtime_template_assets(html_text, asset_page_url)

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

            # Resolve absolute-path hrefs (e.g. /favicon.ico) against the
            # document base. When <base href> changes origin, HTML semantics
            # use that origin rather than the URL that served this document.
            if href.startswith("/") and not href.startswith("//"):
                href_resolved = self._normalize_remote_url(href, asset_page_url)
                if href_resolved:
                    tag["href"] = href_resolved
                    href = href_resolved

            href_lower = href.lower().split("?")[0]  # strip query string for ext check

            if "stylesheet" in rel_values:
                self._set_attr_local(tag, "href", asset_page_url, local_html, preferred_kind="css")

            elif rel_values & {"icon", "button control", "apple-touch-icon",
                               "apple-touch-icon-precomposed", "mask-icon",
                               "image_src"}:
                localized = self._set_attr_local(
                    tag, "href", asset_page_url, local_html, preferred_kind="images",
                )
                if not localized:
                    # Icons are optional presentation metadata. A missing
                    # favicon must not survive as a broken online dependency
                    # or be rediscovered later as a placeholder-worthy image.
                    failed_url = self._normalize_remote_url(href, asset_page_url)
                    if failed_url:
                        self._failed_items = [
                            item for item in self._failed_items
                            if item.get("url") != failed_url
                        ]
                    tag.decompose()
                    logger.debug("  Optional icon unavailable; removed from archive: %s", href)
                    continue

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
                self._set_attr_local(
                    tag, "href", asset_page_url, local_html, preferred_kind="json",
                )

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

        self._patch_local_audio_scripts()

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

        # Lazy-loading libraries use non-standard attributes and copy them to
        # src/srcset only after intersection or user interaction.  Downloading
        # the bytes without rewriting these values still leaves the offline
        # page dependent on the live site, so localize the common variants.
        lazy_attributes = {
            "data-src": "",
            "data-lazy-src": "",
            "data-original": "",
            "data-lazy": "",
            "data-poster": "images",
            "data-background": "images",
            "data-background-image": "images",
            "data-bg": "images",
        }
        for tag in soup.find_all(True):
            for attr, default_kind in lazy_attributes.items():
                if not tag.get(attr):
                    continue
                kind = default_kind
                if not kind:
                    if tag.name in {"img", "picture"}:
                        kind = "images"
                    elif tag.name in {"audio", "video", "source", "track"}:
                        kind = "media"
                    elif tag.name == "script":
                        kind = "js"
                self._set_attr_local(tag, attr, asset_page_url, local_html, preferred_kind=kind)
            if tag.get("data-srcset"):
                self._set_attr_local(
                    tag, "data-srcset", asset_page_url, local_html,
                    preferred_kind="images",
                )

        # Less-common native resource elements are still used by custom CYOA
        # viewers for captions, image buttons, and embedded downloadable media.
        for tag in soup.find_all("track", src=True):
            self._set_attr_local(tag, "src", asset_page_url, local_html, preferred_kind="media")
        for tag in soup.find_all("embed", src=True):
            self._set_attr_local(tag, "src", asset_page_url, local_html)
        for tag in soup.find_all("object", data=True):
            self._set_attr_local(tag, "data", asset_page_url, local_html)
        for tag in soup.find_all("input", src=True):
            if str(tag.get("type") or "").lower() == "image":
                self._set_attr_local(tag, "src", asset_page_url, local_html, preferred_kind="images")

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

        # Some server-rendered Next.js games cannot hydrate without their live
        # backend even when every client chunk is present. Preserve the narrow,
        # self-contained dice control used by accessible roulette/game pages.
        # The fallback waits for React first and only acts when the status did
        # not change, so a functioning application remains authoritative.
        if (
            soup.find("button", attrs={"aria-label": re.compile(r"^Roll dice again$", re.I)})
            and not soup.find(attrs={"data-cyoa-offline-dice-fallback": True})
        ):
            fallback = soup.new_tag("script")
            fallback["data-cyoa-offline-dice-fallback"] = ""
            fallback.string = r"""(()=>{
const findStatus=()=>document.querySelector('[role="status"][aria-label^="Dice results:"]');
const placeholder=s=>/[?\u2013-]\s*$/.test((s?.getAttribute('aria-label')||'').trim());
const localRoll=(status)=>{
  if(!status)return;
  const value=1+Math.floor(Math.random()*6);
  const children=Array.from(status.children);
  if(children.length)children[children.length-1].textContent=String(value);
  const label=status.getAttribute('aria-label')||'Dice results:';
  status.setAttribute('aria-label',label.replace(/(?:\?|\u2013|-|\d+)\s*$/,String(value)));
};
document.addEventListener('click',event=>{
  const button=event.target.closest&&event.target.closest('button[aria-label="Roll dice again"]');
  if(!button)return;
  const status=findStatus(),before=status?.getAttribute('aria-label')||'';
  setTimeout(()=>{if(status&&(status.getAttribute('aria-label')||'')===before)localRoll(status)},120);
});
setTimeout(()=>{const status=findStatus();if(placeholder(status))localRoll(status)},250);
})()"""
            (soup.body or soup).append(fallback)

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
        stub_local = _safe_join(self.output_folder, "js/youtube-iframe-api-stub.js")
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
        root_local = _safe_join(self.output_folder, "project.json")
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
                alias_paths.add(_safe_join(self.output_folder, basename, fallback="project"))

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

        has_project = bool(project_url)
        report_text = format_backup_report_text(
            start_url=self.start_url,
            project_url=project_url,
            project_root="project.json" if has_project else "",
            project_aliases=self._project_aliases if has_project else [],
            downloaded=success,
            failed=failed,
            downloaded_groups=grouped_success,
            failed_groups=grouped_failed,
            notes=(
                ["Engine mode: standard website", "Project payload written to project.json root."]
                if has_project else
                ["Engine mode: pure website", "Project discovery was intentionally skipped."]
            ),
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

        manifest_path = _safe_join(self.output_folder, "backup_report.txt")
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
