"""Project URL discovery, script scanning, and output auto-detection helpers.

Phase 15 moved the low-risk discovery helpers here physically. Larger live
network orchestration helpers still delegate to legacy lazily so importing this
module does not pull the whole legacy module unless those compatibility paths
are used.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from ..config.settings import _load_settings
from ..core.url_utils import canonicalize_url, _directory_base_url
from ..core.atomic_io import validate_response_content_length
from ..core.cancellation import _emit_progress_event, _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from ..network.fetch import fetch_response
from ..network.throttle import _throttle_bandwidth
from ..download.asset_scan import _safe_response_text
from ..integrations.ai_core import (
    AIUsageBudget, _ai_is_available, _ai_mode_allows, _get_ai_provider,
    _normalize_ai_mode, _normalize_ai_provider, _ssrf_block_cross_origin,
)
from ..integrations.ai_calls import _ai_detect_project_json
from .parse import (
    try_decode_bytes, extract_embedded_project_from_js,
    extract_project_from_archive_bytes, extract_project_text_from_payload,
)

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - mirrors legacy fallback behavior
    def BeautifulSoup(*_args, **_kwargs):  # type: ignore
        raise RuntimeError(
            "Missing dependency: beautifulsoup4 is required for HTML/ICC parsing. "
            "Install it with: pip install beautifulsoup4"
        )




def get_iframe_url_from_cyoa_cafe(*args, **kwargs):
    from .cyoa_cafe import get_iframe_url_from_cyoa_cafe as _f
    return _f(*args, **kwargs)

def _legacy():
    """Import legacy lazily to avoid an import cycle during legacy bootstrap."""
    from ..runtime import surface as _surface
    return _surface


def _get_source(url: str, extra_headers: Optional[Dict] = None) -> Optional[str]:
    """Internal indirection used by script discovery; points to the domain implementation."""
    return get_source(url, extra_headers=extra_headers)


def extract_placeholder_url(source: str) -> List[str]:
    p = r'\$store\.commit\("loadApp",.*?\)\}\},e\.open\("GET","(.*?)",!0\)'
    result = re.findall(p, source)
    if result:
        return result
    return re.findall(r'e\.open\(\s*["\']GET["\']\s*,\s*["\']([^"\']+)["\']', source)


def find_candidate_urls_in_text(text: str, base_url: str) -> List[str]:
    candidates: List[str] = []
    seen: Set[str] = set()

    def add(candidate: str) -> None:
        # Minified JS/JSON commonly escapes slashes as \/
        # (JSON.stringify default). Without unescaping, "https:\/\/cdn..." and
        # "assets\/project.json" became literal-backslash URLs that could never
        # fetch, so project detection silently failed on those bundles.
        candidate = candidate.replace("\\/", "/")
        candidate = candidate.strip().strip('"\'')
        if not candidate or candidate.startswith(("data:", "javascript:", "#")):
            return
        full = candidate
        if not full.startswith(("http://", "https://")):
            full = urljoin(base_url, full)
        if _ssrf_block_cross_origin(full, base_url):
            logger.warning(f"Blocked project candidate on internal host: {full}")
            return
        if full not in seen:
            seen.add(full)
            candidates.append(full)

    quoted_re = re.compile(
        r'(?P<quote>["\'])(?P<url>(?:https?:)?//[^"\']+|(?:\./|\.\./|/)?[^"\']+)(?P=quote)',
        re.IGNORECASE,
    )
    _PROJECT_FILENAME_RE = re.compile(
        r'(?:^|/)project(?:\.[a-z0-9]+)?$', re.IGNORECASE
    )
    for m in quoted_re.finditer(text):
        candidate = m.group("url")
        path = urlparse(candidate).path.lower()
        ext = os.path.splitext(path)[1]
        # Only accept candidates with a recognised data extension, or paths that
        # literally end with "project.json" / "project.txt" / "project.zip".
        # Avoid false positives like "Load/Save Project" or UI label strings.
        if ext in {".json", ".txt", ".zip"}:
            add(candidate)
        elif _PROJECT_FILENAME_RE.search(path) and "/" in path:
            add(candidate)

    for candidate in extract_placeholder_url(text):
        add(candidate)

    generic_call_re = re.compile(
        r'(?:fetch|axios\.get|axios\(|load|request)\s*\(\s*["\']([^"\']+\.(?:json|txt|zip))["\']',
        re.IGNORECASE,
    )
    # XHR pattern: e.open("GET","project.json",!0) — URL is 2nd arg, not 1st
    # Also catches: XMLHttpRequest.open("GET","...",true)
    xhr_call_re = re.compile(
        r'\.open\s*\(\s*["\']GET["\']\s*,\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    # Vuex-specific: $store.commit("loadApp",...),e.open("GET","...",!0)
    vuex_xhr_re = re.compile(
        r'\$store\.commit\(["\']loadApp["\'].*?\).*?\.open\(["\']GET["\']\s*,\s*["\']([^"\']+)["\']',
        re.DOTALL | re.IGNORECASE,
    )
    for m in generic_call_re.finditer(text):
        add(m.group(1))
    for m in xhr_call_re.finditer(text):
        add(m.group(1))
    for m in vuex_xhr_re.finditer(text):
        add(m.group(1))

    return candidates[:80]


def _script_priority(label: str) -> Tuple[int, str]:
    lower = label.lower()
    if any(part in lower for part in ["app.", "/app.", "app.js", "/js/app", "main.", "/main.", "main.js", "/index.", "runtime."]):
        return (0, lower)
    if any(part in lower for part in ["chunk-vendors", "vendors", "vendor.", "polyfills", "webpack"]):
        return (2, lower)
    if lower.startswith("inline_script_"):
        return (1, lower)
    return (1, lower)


def extract_app_js_path(code: str) -> str:
    m = re.search(r"js/app\.[^'\"]+\.js", code)
    return m.group(0) if m else ""


def find_script_sources(html_source: str, base_url: Optional[str] = None) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html_source, "html.parser")
    results: List[Tuple[str, str]] = []

    for index, script in enumerate(soup.find_all("script"), start=1):
        if "document.createElement" in str(script):
            src = extract_app_js_path(str(script))
            if src:
                if "\\/" in src:
                    src = src.replace("\\/", "/")
                if base_url and not src.startswith(("http://", "https://")):
                    src = urljoin(base_url, src)
                if base_url and _ssrf_block_cross_origin(src, base_url):
                    logger.warning(f"Blocked script on internal host: {src}")
                    continue
                script_source = _get_source(src)
                if script_source:
                    results.append((src, script_source))
        elif script.get("src"):
            src = script["src"]
            # Same unescape for escaped src attributes that slipped into HTML
            # from JS-rendered templates.
            if "\\/" in src:
                src = src.replace("\\/", "/")
            if base_url and not src.startswith(("http://", "https://")):
                src = urljoin(base_url, src)
            if base_url and _ssrf_block_cross_origin(src, base_url):
                logger.warning(f"Blocked script on internal host: {src}")
                continue
            script_source = _get_source(src)
            if script_source:
                results.append((src, script_source))
        else:
            inline = script.string or script.get_text() or ""
            if inline.strip():
                results.append((f"inline_script_{index}", inline))

    # Some viewers ship only a tiny bootstrap script in HTML and inject the
    # real Vite/Webpack app bundle at runtime. Scan already-loaded scripts for
    # the same app.*.js reference and include that bundle in the source list.
    # This is intentionally bounded and deduplicated: it handles bootstrap ->
    # app chains without turning project discovery into an unrestricted crawl.
    seen_script_urls = {
        label for label, _content in results
        if label.startswith(("http://", "https://"))
    }
    pending = list(results)
    while pending and len(results) < 64:
        owner_label, owner_source = pending.pop(0)
        dynamic_path = extract_app_js_path(owner_source)
        if not dynamic_path:
            continue
        dynamic_url = dynamic_path.replace("\\/", "/")
        if base_url and not dynamic_url.startswith(("http://", "https://")):
            # CYOA Plus core loaders construct paths from the viewer root, not
            # from the directory containing core.js (which would duplicate /js).
            dynamic_url = urljoin(base_url, dynamic_url)
        if base_url and _ssrf_block_cross_origin(dynamic_url, base_url):
            logger.warning(f"Blocked dynamic script on internal host: {dynamic_url}")
            continue
        if dynamic_url in seen_script_urls:
            continue
        dynamic_source = _get_source(dynamic_url)
        if dynamic_source:
            seen_script_urls.add(dynamic_url)
            result = (dynamic_url, dynamic_source)
            results.append(result)
            pending.append(result)

    results.sort(key=lambda item: _script_priority(item[0]))
    return results


def find_scripts(html_source: str, base_url: Optional[str] = None) -> List[str]:
    return [content for _, content in find_script_sources(html_source, base_url)]


def extract_iframe_urls(html_source: str) -> List[str]:
    soup = BeautifulSoup(html_source, "html.parser")
    return [t.get("src") for t in soup.find_all("iframe") if t.get("src")]


def get_first_folder_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


def strip_document_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if not path.endswith("/"):
        path = "/".join(path.split("/")[:-1])
        path = (path + "/") if path else "/"
    return urlunparse(parsed._replace(path=path, query=""))


def _scan_html_for_project_hints(html: str, page_url: str, base_url: str) -> List[str]:
    """
    Fast scan of HTML for strong clues about the project file location.
    Returns deduplicated candidate URLs to try *before* brute-forcing.
    Covers: <meta>, data-* attrs, <link rel="preload">, inline window.__ assignments,
    and inline fetch()/axios() calls pointing at .json/.txt/.zip files.
    """
    hints: List[str] = []
    seen: Set[str] = set()

    def add(raw: str) -> None:
        # Unescape JSON-escaped slashes from inline JS
        # ("data\/project.json") so the hint URL is fetchable (class rev6-10).
        raw = (raw or "").replace("\\/", "/").strip().strip("\"'")
        if not raw or raw.startswith(("data:", "javascript:", "#")):
            return
        full = raw if raw.startswith(("http://", "https://")) else urljoin(base_url, raw)
        if _ssrf_block_cross_origin(full, page_url):
            logger.warning(f"Blocked HTML project hint on internal host: {full}")
            return
        if full not in seen:
            seen.add(full)
            hints.append(full)

    soup = BeautifulSoup(html, "html.parser")

    # <meta name="project-url" content="...">
    for tag in soup.find_all("meta"):
        if re.search(r"project|cyoa", tag.get("name", ""), re.IGNORECASE):
            add(tag.get("content", ""))

    # data-project / data-src / data-url / data-file pointing at data files
    for tag in soup.find_all(True):
        for attr in ("data-project", "data-src", "data-url", "data-file"):
            val = tag.get(attr, "")
            if val and any(val.lower().endswith(e) for e in (".json", ".txt", ".zip")):
                add(val)

    # <link rel="preload" as="fetch" href="...json">
    for tag in soup.find_all("link"):
        href = tag.get("href", "")
        if href and any(href.lower().endswith(e) for e in (".json", ".txt", ".zip")):
            add(href)

    # inline <script>: window.__X__ = "url" and fetch/axios patterns
    _hint_re = re.compile(
        r'(?:'
        r'window\.__(?:PROJECT|APP|DATA|CYOA|INITIAL_STATE)__\s*=\s*["\']([^"\']+)["\']'
        r'|(?:fetch|axios\.get|axios\(|open)\s*\(\s*["\']([^"\']+\.(?:json|txt|zip))["\']'
        r')',
        re.IGNORECASE,
    )
    for script in soup.find_all("script", src=False):
        for m in _hint_re.finditer(script.string or ""):
            add(m.group(1) or m.group(2))

    return hints


def build_default_project_candidates(url: str) -> List[str]:
    """
    Build a prioritised list of candidate project.json URLs for Phase 1.

    Strategy:
      1. Immediate:  project.json at exactly the page's directory
      2. Ancestors:  walk UP the URL path (site.com/a/b/c/ → /a/b/ → /a/ → /)
      3. Subdirs:    common sub-directory names at each ancestor level
      4. Alt names:  alternative filename patterns
      5. Domain:     host-specific known structures (neocities, github.io, etc.)

    Ordering is intentional — most-likely paths come first so Phase 1's
    parallel HEAD-check can short-circuit early on a hit.
    """
    base_url = strip_document_from_url(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # ── canonical names ordered by frequency in the wild ──────────────
    PRIMARY_NAMES = ["project.json", "project.txt", "project.zip"]
    ALT_NAMES = [
        "data.json", "app.json", "cyoa.json", "content.json",
        "index.json", "iccplus.json", "data.txt", "main.json",
        "game.json", "story.json", "adventure.json", "choices.json",
        "app.data.json", "project.data.json",
    ]
    ALL_NAMES = PRIMARY_NAMES + ALT_NAMES

    # ── sub-directories to probe at each level ─────────────────────────
    PRIMARY_SUBDIRS = [
        "",            # root of current dir (first!)
        "app/",
        "data/",
        "assets/",
        "project/",
    ]
    EXTRA_SUBDIRS = [
        "public/",
        "dist/",
        "src/",
        "static/",
        "files/",
        "content/",
        "json/",
        "resources/",
        "media/",
        "game/",
        "cyoa/",
        "viewer/",
        "js/data/",
        "js/",
        "scripts/",
        "config/",
    ]
    ALL_SUBDIRS = PRIMARY_SUBDIRS + EXTRA_SUBDIRS

    seen: Set[str] = set()
    result: List[str] = []

    def add(u: str) -> None:
        u = u.split("?")[0].split("#")[0]   # strip query/fragment
        if u not in seen:
            seen.add(u)
            result.append(u)

    # ── 1. Immediate base URL ─────────────────────────────────────────
    for name in PRIMARY_NAMES:
        add(urljoin(base_url, name))

    # ── 2. Walk UP the URL path → try project.json at every ancestor ──
    #   e.g. /games/cyoa/isekai/ → /games/cyoa/ → /games/ → /
    path_parts = [p for p in parsed.path.rstrip("/").split("/") if p]
    # Strip trailing filename (e.g. index.html) — only keep directory components
    if path_parts and "." in path_parts[-1] and not path_parts[-1].startswith("."):
        path_parts = path_parts[:-1]
    ancestor_bases: List[str] = []

    for depth in range(len(path_parts), 0, -1):
        ancestor_path = "/" + "/".join(path_parts[:depth]) + "/"
        ancestor_base = urlunparse(parsed._replace(
            path=ancestor_path, query="", fragment=""))
        ancestor_bases.append(ancestor_base)

    # Add root too
    root_base = urlunparse(parsed._replace(path="/", query="", fragment=""))
    if root_base != base_url:
        ancestor_bases.append(root_base)

    for ancestor in ancestor_bases:
        for name in PRIMARY_NAMES:
            add(urljoin(ancestor, name))

    # ── 3. Primary subdirs at immediate base ──────────────────────────
    for subdir in PRIMARY_SUBDIRS[1:]:   # skip "" already done above
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, subdir + name))

    # ── 4. Primary subdirs at each ancestor ───────────────────────────
    for ancestor in ancestor_bases[:3]:  # only first 3 ancestors (most relevant)
        for subdir in PRIMARY_SUBDIRS[1:]:
            for name in PRIMARY_NAMES:
                add(urljoin(ancestor, subdir + name))

    # ── 5. Alt names at base + common ancestor ────────────────────────
    for name in ALT_NAMES:
        add(urljoin(base_url, name))
    if ancestor_bases:
        for name in ALT_NAMES:
            add(urljoin(ancestor_bases[0], name))

    # ── 6. Extra subdirs at base ──────────────────────────────────────
    for subdir in EXTRA_SUBDIRS:
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, subdir + name))

    # ── 7. Alt names at primary subdirs ──────────────────────────────
    for subdir in PRIMARY_SUBDIRS:
        for name in ALT_NAMES:
            add(urljoin(base_url, subdir + name))

    # ── 8. Domain-specific patterns ──────────────────────────────────
    #   Different hosting platforms have known project.json locations
    if host.endswith(".neocities.org") or host == "neocities.org":
        # Neocities: flat structure common, also /cyoa/ subfolder
        for name in PRIMARY_NAMES:
            add(f"https://{host}/{name}")
            add(f"https://{host}/cyoa/{name}")
            add(f"https://{host}/game/{name}")

    elif host.endswith(".github.io"):
        # GitHub Pages: /docs/, /public/, /<repo-name>/
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, f"docs/{name}"))
            add(urljoin(base_url, f"gh-pages/{name}"))
            add(f"https://{host}/{name}")
        # Try repo subpath: user.github.io/repo/ → try /repo/project.json
        if len(path_parts) >= 1:
            repo = path_parts[0]
            for name in PRIMARY_NAMES:
                add(f"https://{host}/{repo}/{name}")

    elif "itch.io" in host:
        # itch.io: game files in /game/ or /public/
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, f"game/{name}"))
            add(urljoin(base_url, f"public/{name}"))

    elif host.endswith(".cyoa.cafe") or host == "cyoa.cafe":
        # cyoa.cafe subdomains: project.json usually at slug root
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, name))
            if path_parts:
                add(f"https://{host}/{path_parts[0]}/{name}")

    # ── 9. Case variant: Project.json (capital P — some Windows servers) ──
    for base in [base_url] + ancestor_bases[:2]:
        add(urljoin(base, "Project.json"))
        add(urljoin(base, "PROJECT.JSON"))

    return result


# Larger full-project orchestration still uses the compatibility surface during
# de-legacy migration. Low-risk live probe helpers below are now owned here.
def try_project_candidate(
    candidate_url: str,
    label: str = "",
    quiet: bool = False,
    source_url: str = "",
) -> Tuple[Optional[str], str]:
    """Fetch and validate a project candidate with live transfer telemetry.

    v46.8 streams candidate payloads instead of reading ``response.content`` in
    one opaque step. This keeps the GUI speed/byte counters active for large
    project.json/project.txt/project.zip files without changing the public
    return contract.
    """
    if source_url and _ssrf_block_cross_origin(candidate_url, source_url):
        logger.warning(f"Blocked project candidate on internal host: {candidate_url}")
        return None, ""
    if label:
        logger.info(f"Trying {label}: {candidate_url}")
    else:
        logger.info(f"Trying candidate: {candidate_url}")

    response = fetch_response(candidate_url, timeout=25, quiet=quiet, stream=True)
    if not response:
        return None, ""

    headers = getattr(response, "headers", {}) or {}
    raw_total = headers.get("Content-Length") or headers.get("content-length")
    try:
        total = int(raw_total) if raw_total not in (None, "") else None
    except (TypeError, ValueError):
        total = None

    display_url = str(getattr(response, "url", None) or candidate_url)
    _emit_progress_event(
        "file_started",
        name=display_url,
        url=display_url,
        total_bytes=total,
    )
    chunks: List[bytes] = []
    downloaded = 0
    try:
        iterator = response.iter_content(chunk_size=128 * 1024)
        for chunk in iterator:
            _raise_if_cancelled()
            if not chunk:
                continue
            chunks.append(chunk)
            downloaded += len(chunk)
            # Publish absolute byte progress directly. The legacy bandwidth
            # callback is disabled for this chunk so total bytes and speed are
            # not double-counted. This is required during auto-detect, where
            # project.json can still be streaming long after the HTTP headers
            # (and the legacy "Downloaded" log line) were received.
            _throttle_bandwidth(len(chunk), record_gui=False)
            _emit_progress_event(
                "file_progress",
                downloaded=downloaded,
                total=total,
                url=display_url,
                name=os.path.basename(urlparse(display_url).path) or display_url,
            )
        raw = b"".join(chunks)
        validate_response_content_length(response, downloaded)
        _emit_progress_event(
            "file_completed",
            name=os.path.basename(urlparse(display_url).path) or display_url,
            url=display_url,
        )
    except Exception:
        _emit_progress_event(
            "file_failed",
            name=os.path.basename(urlparse(display_url).path) or display_url,
            url=display_url,
            error="Project candidate download failed",
        )
        raise
    finally:
        try:
            response.close()
        except Exception as close_exc:
            logger.debug(f"Could not close project candidate response: {close_exc}")

    archived = extract_project_from_archive_bytes(raw, candidate_url)
    if archived:
        logger.info(f"Resolved project from archive-like payload: {candidate_url}")
        return archived, candidate_url

    text_payload = try_decode_bytes(raw)
    project_text = extract_project_text_from_payload(text_payload)
    if project_text:
        logger.info(f"Resolved project payload from text candidate: {candidate_url}")
        return project_text, candidate_url

    return None, ""


def get_project_source(url: str, depth: int = 0, ai_api_key: str = "",
                       ai_provider: str = "", ai_mode: str = "auto_fallback",
                       ai_budget: Optional[AIUsageBudget] = None) -> Tuple[Optional[str], str]:
    if depth > 4:
        logger.warning(f"Max recursion depth at {url}")
        return None, ""

    def _is_cafe_url(value: str) -> bool:
        host = (urlparse(value).hostname or "").lower()
        return host == "cyoa.cafe" or host.endswith(".cyoa.cafe")

    # Only catalog URLs need metadata/iframe resolution. A creator subdomain is
    # already the authoritative viewer; resolving it through the catalog API
    # again adds duplicate page/project requests without improving accuracy.
    if (urlparse(url).hostname or "").lower() == "cyoa.cafe":
        logger.info("cyoa.cafe detected, resolving real URL…")
        for _resolve_attempt in range(3):
            try:
                resolved = get_iframe_url_from_cyoa_cafe(url)
            except Exception as e:
                logger.error(f"cyoa.cafe resolve error: {e}")
                return None, ""

            if not resolved or resolved == url:
                logger.info("cyoa.cafe: using resolved URL directly")
                break

            logger.info(f"cyoa.cafe resolved → {resolved}")
            url = resolved

            # Stop if URL has left cyoa.cafe entirely
            if not _is_cafe_url(url):
                break

            # Stop if URL is now on a SUBDOMAIN of cyoa.cafe (e.g.
            # lordcyoa.cyoa.cafe/isekai-adventures/) — that IS the
            # hosted CYOA, not another redirect layer to follow.
            parsed_resolved = urlparse(url)
            if (parsed_resolved.hostname or "").lower() != "cyoa.cafe":
                logger.info(
                    f"cyoa.cafe: resolved to subdomain host "
                    f"({parsed_resolved.netloc}) — using as final CYOA URL"
                )
                break


    # ── cyoa.cafe React shell detection ──────────────────────────────
    # Some cyoa.cafe subdomains serve a React SPA shell at /slug/ while
    # the actual ICC Plus viewer is at /slug/game/.
    # Detect by: loads "game/assets/*.js" + has <div id="root"> (React).
    _shell_r = None
    try:
        _shell_r = fetch_response(url, extra_headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        _shell_html = _safe_response_text(_shell_r) if _shell_r is not None else ""
        if ('game/assets/' in _shell_html and
                ('<div id="root">' in _shell_html or
                 'placeholder-content' in _shell_html) and
                'id="app"' not in _shell_html and
                'js/app.js' not in _shell_html):
            _game_url = url.rstrip('/') + '/game/'
            logger.info(f"cyoa.cafe React shell detected → redirect to {_game_url}")
            url = _game_url
    except Exception as _se:
        logger.debug(f"cyoa.cafe shell check failed: {_se}")
    finally:
        if _shell_r is not None:
            try:
                _shell_r.close()
            except Exception:
                pass

    logger.info(f"Project search start: {url}")
    base_url = strip_document_from_url(url)

    # ── Phase 0: fetch HTML once (reused by all later phases) ─────────────
    logger.info("Phase 0: fetching page HTML…")
    source = get_source(url)

    # ── Phase 0b: scan HTML for explicit hints — try these first ──────────
    if source:
        html_hints = _scan_html_for_project_hints(source, url, base_url)
        if html_hints:
            logger.info(f"Phase 0b: found {len(html_hints)} HTML hint(s) — trying before brute-force…")
            for hint in html_hints:
                proj, proj_url = try_project_candidate(hint, label="HTML hint", source_url=url)
                if proj:
                    return proj, proj_url

    # ── Phase 0c: try the canonical default locations first ───────────────
    # A number of static hosts expose only a bootstrap script in HTML. The
    # actual viewer bundle then contains the project object, so checking the
    # large list of conventional project paths first just adds avoidable
    # latency (and can make the download look stuck on hosts that time out).
    # Probe runtime-looking script loaders early and keep the result for the
    # full script phase below.
    script_sources: List[Tuple[str, str]] = []
    if source and re.search(
        r"core\.js|document\.createElement|<script\b[^>]*\btype\s*=\s*[\"']module",
        source,
        re.IGNORECASE,
    ):
        logger.info("Phase 0d: checking runtime-loaded script bundles...")
        script_sources = find_script_sources(source, base_url)

    # This makes the control flow match user expectations and avoids waiting
    # for a large brute-force sweep when /project.json exists.
    default_candidates = build_default_project_candidates(url)
    canonical_defaults: List[str] = []
    for _candidate in default_candidates:
        _path = urlparse(_candidate).path.lower().rstrip("/")
        if _path.endswith(("/project.json", "/project.txt", "/project.zip")):
            canonical_defaults.append(_candidate)
        if len(canonical_defaults) >= 3:
            break
    if canonical_defaults:
        logger.info("Phase 0c: trying canonical default project locations first…")
        for candidate in canonical_defaults:
            proj, proj_url = try_project_candidate(candidate, label="default path", quiet=True, source_url=url)
            if proj:
                return proj, proj_url

    # ── Phase 1: parallel-check remaining default candidates ───────────────
    # A runtime bundle may contain a viewer's default editor state before the
    # actual project is loaded. Prefer an explicit project file above; only
    # fall back to embedded JS after canonical project endpoints failed.
    for script_label, js_script in script_sources:
        embedded = extract_embedded_project_from_js(js_script)
        if embedded:
            logger.info(f"  Embedded project found in runtime script: {script_label}")
            return embedded, url

    default_candidates = [c for c in default_candidates if c not in set(canonical_defaults)]
    logger.info(
        f"Phase 1: checking {len(default_candidates)} remaining default candidates in parallel…"
    )
    live = _parallel_head_check(default_candidates, max_workers=12)
    logger.info(f"  {len(live)}/{len(default_candidates)} candidate(s) alive — fetching…")
    for candidate in live:
        proj, proj_url = try_project_candidate(candidate, label="default path", source_url=url)
        if proj:
            return proj, proj_url

    if not source:
        logger.warning("Could not download page HTML.")
        return None, ""

    # ── Phase 2: deeper scan of HTML text ────────────────────────────────
    logger.info("Phase 2: scanning HTML text for candidate file references…")
    html_candidates = find_candidate_urls_in_text(source, base_url)
    if html_candidates:
        logger.info(f"  Found {len(html_candidates)} candidate URL(s) in HTML.")
    for idx, candidate in enumerate(html_candidates, start=1):
        logger.info(f"  HTML candidate {idx}/{len(html_candidates)}")
        proj, proj_url = try_project_candidate(candidate, label="HTML-discovered file", source_url=url)
        if proj:
            return proj, proj_url

    # ── Phase 3: scan JS bundles and inline scripts ───────────────────────
    logger.info("Phase 3: scanning script bundles and inline JS…")
    if not script_sources:
        script_sources = find_script_sources(source, base_url)
    logger.info(f"  Found {len(script_sources)} script block(s)/bundle(s) to scan.")

    for idx, (script_label, js_script) in enumerate(script_sources, start=1):
        logger.info(f"  Scanning script {idx}/{len(script_sources)}: {script_label}")

        js_candidates = find_candidate_urls_in_text(js_script, base_url)
        if js_candidates:
            logger.info(f"    {len(js_candidates)} candidate file reference(s) in script.")
        for c_idx, candidate in enumerate(js_candidates, start=1):
            logger.info(f"    JS candidate {c_idx}/{len(js_candidates)}")
            proj, proj_url = try_project_candidate(candidate, label="JS-discovered file", source_url=url)
            if proj:
                return proj, proj_url

        embedded = extract_embedded_project_from_js(js_script)
        if embedded:
            logger.info(f"  Embedded project found in: {script_label}")
            # Return the *page* URL, not the JS file URL, so that relative
            # image paths resolve from the site root (e.g. /images/)
            # rather than the JS directory (e.g. /js/images/).
            return embedded, url

    # ── Phase 4: iframes ─────────────────────────────────────────────────
    logger.info("Phase 4: checking iframes…")
    iframe_urls = extract_iframe_urls(source)
    for idx, iframe_url in enumerate(iframe_urls, start=1):
        iframe_full = urljoin(base_url, iframe_url)
        if _ssrf_block_cross_origin(iframe_full, url):
            logger.warning(f"Blocked iframe on internal host: {iframe_full}")
            continue
        logger.info(f"  Checking iframe {idx}/{len(iframe_urls)}: {iframe_full}")
        proj, proj_url = get_project_source(iframe_full, depth + 1, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget)
        if proj:
            return proj, proj_url

    # ── Phase 5: AI-assisted detection (provider-neutral) ─────────────
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode)
    if depth == 0 and _ai_mode_allows("project_detect", ai_mode) and _ai_is_available(ai_api_key, ai_provider):
        logger.info("Phase 5: AI-assisted project detection…")
        ai_candidate = _ai_detect_project_json(url, source, api_key=ai_api_key, provider=ai_provider, ai_mode=ai_mode, budget=ai_budget)
        if ai_candidate:
            r = None
            try:
                r = fetch_response(ai_candidate, timeout=15, extra_headers={"User-Agent": "Mozilla/5.0"})
                txt = _safe_response_text(r) if r is not None else ""
                if txt.strip()[:1] in ("{", "["):
                    logger.info(f"  AI candidate confirmed: {ai_candidate}")
                    return txt, ai_candidate
            except Exception as e:
                logger.debug(f"  AI candidate failed: {e}")
            finally:
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass

    logger.warning("Project search finished without result.")
    return None, ""


def get_source(url: str, extra_headers: Optional[Dict] = None) -> Optional[str]:
    """Fetch URL and return text content using explicit byte decoding.

    requests.response.text can default to ISO-8859-1 when a server omits a
    charset, which breaks Asian text in CYOA project files. Keep the legacy
    behavior by decoding response.content through try_decode_bytes().
    """
    response = fetch_response(url, extra_headers=extra_headers, timeout=20)
    if not response:
        return None
    try:
        return try_decode_bytes(response.content)
    finally:
        try:
            response.close()
        except Exception:
            pass


def url_file_exists(url: str, timeout: int = 5) -> bool:
    try:
        r = fetch_response(
            url, timeout=timeout, stream=True,
            extra_headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-4095"},
        )
        try:
            return bool(r is not None and r.status_code in {200, 206})
        finally:
            close = getattr(r, "close", None) if r is not None else None
            if callable(close):
                close()
    except Exception:
        return False


def _parallel_head_check(
    candidates: List[str],
    max_workers: int = 12,
    timeout: int = 5,
) -> List[str]:
    """Check candidate URLs through the unified fetch wrapper.

    This intentionally uses lightweight GET through fetch_response instead of
    raw HEAD so Cloudflare/FlareSolverr, proxy, DNS, and retry policy are
    consistent.
    """
    results: Dict[str, bool] = {}
    lock = threading.Lock()
    if not candidates:
        return []
    max_workers = max(1, min(32, int(max_workers) if max_workers else 1))

    def check(url: str) -> None:
        r = None
        try:
            r = fetch_response(
                url, timeout=timeout, stream=True, quiet=True,
                extra_headers={
                    "User-Agent": "Mozilla/5.0",
                    "Range": "bytes=0-4095",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            ok = bool(r is not None and r.status_code in {200, 206})
        except DownloadCancelledError:
            raise
        except Exception:
            ok = False
        finally:
            if r is not None:
                try:
                    close = getattr(r, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    pass
        with lock:
            results[url] = ok

    ex = ThreadPoolExecutor(max_workers=max_workers)
    futures = [ex.submit(check, url) for url in candidates]
    try:
        for future in as_completed(futures):
            _raise_if_cancelled()
            future.result()
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        ex.shutdown(wait=False, cancel_futures=True)

    return [u for u in candidates if results.get(u)]


def _normalize_auto_detect_output(value: Any) -> str:
    """Normalize saved Auto mode output preference without breaking old settings."""
    v = str(value or "folder").strip().lower().replace("-", "_").replace(" ", "_")
    if v in {"zip", "website_zip", "cyoap_vue_zip", "compressed", "archive"}:
        return "zip"
    return "folder"


def _auto_detect_output_variant(kind: str, output_pref: Optional[str] = None) -> str:
    """Return the concrete Auto mode variant for a detected engine kind."""
    pref = _normalize_auto_detect_output(
        output_pref if output_pref is not None else _load_settings().get("auto_detect_output", "folder")
    )
    if str(kind or "").lower() == "cyoap_vue":
        return "cyoap_vue_zip" if pref == "zip" else "cyoap_vue_folder"
    return "website_zip" if pref == "zip" else "website_folder"


def auto_detect_mode(url: str, timeout: int = 6) -> str:
    """Auto-detect the safest concrete output mode for *url*.

    cyoa.cafe catalogue URLs are resolved before any engine probe. CYOAP Vue is
    accepted only when platform.json and nodes/list.json contain valid JSON of
    the expected types; generic HTTP-200 SPA fallback pages are rejected.
    """
    source_url = canonicalize_url(str(url or "").strip())
    probe_url = source_url
    parsed_source = urlparse(source_url)
    source_host = parsed_source.netloc.lower()
    is_cafe = source_host == "cyoa.cafe" or source_host.endswith(".cyoa.cafe")
    is_cafe_metadata = source_host == "cyoa.cafe" and parsed_source.path.rstrip("/").startswith("/game/")

    logger.info(f"[Auto-detect] Probing: {_directory_base_url(source_url)}")

    if is_cafe_metadata:
        try:
            from .cyoa_cafe import classify_cyoa_cafe_record, fetch_cyoa_cafe_record
            if classify_cyoa_cafe_record(fetch_cyoa_cafe_record(source_url)) == "static_pages":
                detected_mode = _auto_detect_output_variant("website")
                logger.info(
                    "[Auto-detect] → cyoa.cafe static-page record → %s",
                    detected_mode,
                )
                return detected_mode
        except Exception as exc:
            logger.debug("CYOA.CAFE static record probe skipped: %s", exc)

    if is_cafe:
        try:
            resolved = _legacy().get_iframe_url_from_cyoa_cafe(source_url)
            if resolved:
                probe_url = canonicalize_url(resolved)
                if probe_url != source_url:
                    logger.info(f"[Auto-detect] Resolved cyoa.cafe target → {probe_url}")
        except Exception as exc:
            logger.warning(f"[Auto-detect] cyoa.cafe resolver unavailable: {exc}")
            if is_cafe_metadata:
                detected_mode = _auto_detect_output_variant("website")
                logger.info(f"[Auto-detect] → unresolved metadata page; using {detected_mode}")
                return detected_mode

    base = _directory_base_url(probe_url)

    from .cyoap_vue import _probe_cyoap_vue_structure

    if _probe_cyoap_vue_structure(base, timeout=timeout):
        detected_mode = _auto_detect_output_variant("cyoap_vue")
        logger.info(f"[Auto-detect] → cyoap_vue detected → {detected_mode}")
        return detected_mode

    try:
        candidates = build_default_project_candidates(probe_url)
        live = _parallel_head_check(candidates, max_workers=12, timeout=timeout)
        if live:
            detected_mode = _auto_detect_output_variant("website")
            logger.info(
                f"[Auto-detect] → ICC project detected ({len(live)} candidate(s))"
                f" → {detected_mode}"
            )
            return detected_mode
    except DownloadCancelledError:
        raise
    except Exception as exc:
        logger.warning(f"[Auto-detect] Standard project probe failed: {exc}")

    detected_mode = _auto_detect_output_variant("website")
    logger.info(f"[Auto-detect] → defaulting to {detected_mode}")
    return detected_mode


def auto_detect_modes_batch(
    items: List[Dict],
    max_workers: int = 4,
    progress_cb=None,
) -> List[Dict]:
    """Run auto_detect_mode for every item in the batch that has mode == 'auto'."""
    to_probe = [i for i in items if i.get("mode", "embed") == "auto"]
    total = len(to_probe)
    done = {"n": 0}
    lock = threading.Lock()

    def probe_one(item: Dict) -> None:
        try:
            detected = auto_detect_mode(item["url"])
        except DownloadCancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Auto-detect] Error for {item['url']}: {e}")
            detected = "embed"
        item["mode"] = detected
        item["auto_detected"] = True
        with lock:
            done["n"] += 1
            if progress_cb:
                progress_cb(done["n"], total)

    if to_probe:
        logger.info(f"[Auto-detect] Probing {total} URL(s) in parallel (workers={max_workers})…")
        ex = ThreadPoolExecutor(max_workers=max(1, int(max_workers or 1)))
        futures = [ex.submit(probe_one, item) for item in to_probe]
        try:
            for future in as_completed(futures):
                _raise_if_cancelled()
                future.result()
        except BaseException:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            ex.shutdown(wait=False, cancel_futures=True)
        logger.info("[Auto-detect] Done.")

    return items


__all__ = [
    "find_candidate_urls_in_text",
    "try_project_candidate",
    "_script_priority",
    "find_script_sources",
    "_scan_html_for_project_hints",
    "get_project_source",
    "get_source",
    "url_file_exists",
    "_parallel_head_check",
    "_normalize_auto_detect_output",
    "_auto_detect_output_variant",
    "auto_detect_mode",
    "auto_detect_modes_batch",
    "find_scripts",
    "extract_placeholder_url",
    "extract_iframe_urls",
    "get_first_folder_from_url",
    "extract_app_js_path",
    "build_default_project_candidates",
    "strip_document_from_url",
]
