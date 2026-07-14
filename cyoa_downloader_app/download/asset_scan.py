"""Low-risk asset scanning/download helper functions extracted from legacy.py.

These helpers are intentionally behavior-preserving and avoid importing the
legacy module at import time, so they can be used by both the transitional
legacy facade and the domain download pipeline.
"""

from __future__ import annotations

import base64
import hashlib as _hashlib
import json
import os
import re
import threading as _threading
from typing import Dict, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from ..constants.assets import (
    IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, AUDIO_FIELDS, BGMLIST_FIELDS,
    ICC_PLUS_IMAGE_KEYS, IMAGE_FIELDS, _SOUNDCLOUD_URL_RE,
    _YOUTUBE_ID_RE, _YOUTUBE_URL_RE,
)
from ..logging_setup import logger
from ..project.parse import try_decode_bytes

# Raw CDN hosts used by the gallery-dl smart-mode classifier. Kept here as an
# independent constant so this low-level scanner does not need to import the
# transitional gallery_dl bridge (which would pull legacy.py back in).
_RAW_GALLERY_DL_CDN_HOSTS: Set[str] = {
    "i.pximg.net", "img-original.pximg.net", "img-zip-ugoira.pximg.net",
    "pbs.twimg.com", "c.deviantart.com", "a.deviantart.net", "wixmp.com",
    "cdn.donmai.us", "static1.e621.net", "static1.e926.net",
    "img3.sankakucomplex.com", "img.sankakucomplex.com",
    "img1.gelbooru.com", "img2.gelbooru.com", "img.hypnohub.net",
    "img.rule34.xxx", "img3.rule34.xxx", "img.rule34.paheal.net",
}

def _is_probable_raw_cdn_asset(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        ext = os.path.splitext(parsed.path.lower())[1]
        return host in _RAW_GALLERY_DL_CDN_HOSTS or ext in IMAGE_EXTENSIONS
    except Exception as exc:
        logger.debug(f"gallery-dl URL classification failed for {url}: {exc}")
        return True


_image_hash_map = {}   # sha256 → first local path (dedup)
_hash_lock = _threading.Lock()

def _check_image_dedup(content: bytes, local_path: str, scope: str = "") -> Optional[str]:
    """Check for identical content within one output scope."""
    if not content:
        return None
    h = _hashlib.sha256(content).hexdigest()
    key = (scope or "", h)
    with _hash_lock:
        if key in _image_hash_map:
            return _image_hash_map[key]
        _image_hash_map[key] = local_path
    return None


def _make_placeholder_svg(label: str = "") -> bytes:
    """
    Return a minimal SVG placeholder image for use when an image fails to download.
    Shows a grey box with a broken-image icon and the original filename.
    """
    safe_label = (label[:40] + "…") if len(label) > 40 else label
    # Escape XML special chars
    safe_label = safe_label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
        '<rect width="320" height="200" fill="#2a2a2a" rx="6"/>'
        '<line x1="130" y1="70" x2="190" y2="130" stroke="#888" stroke-width="2"/>'
        '<line x1="190" y1="70" x2="130" y2="130" stroke="#888" stroke-width="2"/>'
        '<rect x="120" y="60" width="80" height="80" fill="none" stroke="#666" stroke-width="2" rx="4"/>'
        f'<text x="160" y="165" font-family="monospace" font-size="11" fill="#aaa" '
        f'text-anchor="middle">{safe_label}</text>'
        '</svg>'
    )
    return svg.encode("utf-8")


_PLACEHOLDER_DATA_URI = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(_make_placeholder_svg("[image unavailable]")).decode()
)


def _safe_response_text(r: "requests.Response") -> str:
    """
    Decode response content with correct encoding.
    Always passes through try_decode_bytes() which tries UTF-8 first.
    Server-declared ISO-8859-1 is ignored (it's the HTTP/1.1 default, not a
    real declaration — most servers omit charset and requests fills in latin-1).
    """
    # Only trust the server's declared encoding if it's explicit and NOT
    # the latin-1 default that requests infers from the HTTP spec.
    _IGNORE = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", ""}
    declared = (r.encoding or "").lower()
    pref = r.encoding if declared not in _IGNORE else ""
    return try_decode_bytes(r.content, preferred_encoding=pref)


def _scan_file_for_assets(
    text: str,
    file_url: str,
    base_url: str,
    file_ext: str = ".js",
) -> Set[str]:
    """
    Scan a downloaded JS or CSS file for asset URL references and return
    a set of absolute URLs that should be downloaded.

    Handles:
    - String literals: './assets/img.webp', '../images/foo.png'
    - CSS url(): url("image.webp"), url('../fonts/font.woff2')
    - Bare filenames: 'audio.mp3' (no directory component)
    - Absolute URLs: 'https://cdn.example.com/img.avif'
    - Vite/Webpack lazy chunks: import("./chunk-abc123.js")
    - Template literals with plain path segments
    """
    import re as _re

    # Minified bundles store URLs JSON-escaped ("img\/x.webp").
    # The scan regexes never matched those, so the deep scan silently skipped
    # such assets (same bug class as rev6 in find_candidate_urls_in_text).
    # Scan-side only: this function returns URLs, it never rewrites the file,
    # and the browser unescapes \/ at runtime, so local paths still line up.
    if "\\/" in text:
        text = text.replace("\\/", "/")

    ASSET_EXTS = (
        r'\.(?:webp|avif|png|jpg|jpeg|gif|svg|ico|bmp|tiff'
        r'|mp3|ogg|opus|wav|m4a|aac|flac'
        r'|mp4|webm|mov|ogv|m4v'
        r'|woff2|woff|ttf|otf|eot'
        r'|json|css|wasm)(?:\?[^\s"\'`<>]*)?'
    )
    # JS chunk pattern — matches Vite/Rollup/Webpack hashed chunk names
    JS_CHUNK = r'\.m?js(?:\?[^\s"\'`<>]*)?'
    # Quote chars — double, single, backtick
    Q = r'''["'`]'''

    found: Set[str] = set()
    file_base = file_url.rsplit('/', 1)[0] + '/'   # directory of this file

    # Some viewer bootstraps resolve resources from a path derived from the
    # script URL, then concatenate that path with literals, e.g.
    # ``basePath = new URL('../', currentScript.src)`` followed by
    # ``basePath + 'css/app.css'``. Those literals are not relative to the JS
    # file directory. Follow this explicit browser expression without guessing
    # any author-chosen folder names.
    js_base_prefixes: Dict[str, str] = {}
    base_prefixed_literals: Set[str] = set()
    if file_ext in ('.js', '.mjs', '.cjs'):
        for base_match in _re.finditer(
            r'\b([A-Za-z_$][\w$]*)\s*=\s*new\s+URL\(\s*["\']([^"\']+)["\']\s*,\s*'
            r'(?:currentScript|document\.currentScript)\.src\s*\)',
            text,
            _re.IGNORECASE,
        ):
            js_base_prefixes[base_match.group(1)] = urljoin(
                file_url, base_match.group(2)
            )

    for base_name, explicit_base in js_base_prefixes.items():
        prefixed_re = _re.compile(
            rf'\b{_re.escape(base_name)}\s*\+\s*["\']([^"\']+{ASSET_EXTS})["\']',
            _re.IGNORECASE,
        )
        for prefixed_match in prefixed_re.finditer(text):
            raw = prefixed_match.group(1)
            base_prefixed_literals.add(raw)
            found.add(urljoin(explicit_base, raw))

    inferred_dynamic_assets = _infer_dynamic_asset_paths(text)

    def _resolve(raw: str) -> Optional[str]:
        raw = raw.strip().lstrip()
        if not raw or len(raw) > 400:
            return None
        if raw.startswith(('data:', '#', 'javascript:', 'mailto:',
                            'http://www.w3.org', 'blob:')):
            return None
        if raw.startswith('https://') or raw.startswith('http://'):
            return raw
        if raw.startswith('//'):
            return 'https:' + raw
        if raw.startswith('./') or raw.startswith('../'):
            resolved = urljoin(file_base, raw)
            # ── Fix: Vite encodes asset paths relative to site root, not JS file ──
            # If JS file is inside /assets/ and path starts with ./assets/,
            # urljoin creates /assets/assets/... (double). Correct it.
            # Removed a dead `double = <base path>*2`
            # assignment here — it was computed but never read; the real
            # double-prefix fix uses `double_pat` (built from `seg`) below.
            parsed_r = urlparse(resolved)
            path_r   = parsed_r.path
            # Detect /X/X/ double-prefix pattern (e.g. /assets/assets/)
            base_path = urlparse(base_url).path.rstrip('/')
            if base_path:
                seg = base_path.lstrip('/')
                double_pat = f'/{seg}/{seg}/'
                if double_pat in path_r:
                    path_r  = path_r.replace(double_pat, f'/{seg}/', 1)
                    resolved = urlunparse(parsed_r._replace(path=path_r))
            # Also fix: /assets/assets/ specifically (common Vite pattern)
            if '/assets/assets/' in resolved:
                resolved = resolved.replace('/assets/assets/', '/assets/', 1)
            return resolved
        if raw.startswith('/'):
            parsed_base = urlparse(base_url)
            return f"{parsed_base.scheme}://{parsed_base.netloc}{raw}"
        return urljoin(file_base, raw)

    def _resolve_js_literal(raw: str) -> Optional[str]:
        """Resolve non-explicit JS asset literals from the viewer base.

        A browser fetch such as ``fetch('project.json')`` resolves against the
        document/viewer URL, not the directory containing the JS bundle. Keep
        explicit ``./`` and ``../`` references relative to that bundle.
        """
        if file_ext in ('.js', '.mjs', '.cjs') and not raw.startswith(
            ('./', '../', '/', 'http://', 'https://', '//')
        ):
            return urljoin(base_url.rstrip('/') + '/', raw)
        return _resolve(raw)

    # JavaScript galleries often keep a shared base path separately from a
    # large filename array: ``const imageSrc='image/'; imagesToLoad=['card/x.webp']``.
    # The browser concatenates both pieces, so scanning the array item alone
    # points at a plausible but wrong 404 path.
    for prefixed_values in inferred_dynamic_assets.values():
        for prefixed in prefixed_values:
            r = _resolve(prefixed)
            if r:
                found.add(r)

    # ── CSS url() — scan in ALL file types (CSS, HTML, AND JS) ────────
    # Many JS frameworks embed CSS-in-JS with url() references.
    for m in _re.finditer(
        r'url\(\s*["\']?([^"\')\s]+' + ASSET_EXTS + r'[^"\')\s]*)["\']?\s*\)',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── CSS image-set() — responsive image declarations ───────────────
    for m in _re.finditer(
        r'image-set\([^)]*' + Q + r'([^"\'`]+' + ASSET_EXTS + r')' + Q,
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── String/template literals: "path.ext", 'path.ext', `path.ext` ─
    # CRITICAL: includes single-quotes — many minified JS uses them.
    for m in _re.finditer(
        Q + r'([^"\'`\n\r<>{}()|\\]{1,300}' + ASSET_EXTS + r')' + Q,
        text, _re.IGNORECASE
    ):
        raw = m.group(1)
        if raw.strip() in ('.json', '.mp3', '.webp', '.png', '.js', '.css'):
            continue
        if raw in base_prefixed_literals:
            # Already resolved against the explicit JS base above. Resolving
            # it again against file_base would create paths such as /js/css/.
            continue
        if raw in inferred_dynamic_assets:
            continue
        r = _resolve_js_literal(raw)
        if r: found.add(r)

    # ── Static ES module imports: import{...}from"./foo.js" / import"./foo.js" ──
    # These are NOT dynamic import() calls — they use 'from' or bare import
    for m in _re.finditer(
        r'(?:from|import)\s*["\'](\.[^"\']+' + JS_CHUNK + r')["\']',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── Vite __vite__mapDeps bare filenames ───────────────────────────
    # Vite bundles contain arrays of bare chunk filenames for preloading.
    # These have NO ./ prefix but live in /assets/ (Vite convention).
    # Pattern: __vite__mapDeps([0,1],m=__vite__mapDeps,d=(m.f||(m.f=["foo.js","bar.js"...])))
    _vite_arr = _re.search(
        r'__vite__mapDeps.*?d=\(m\.f\|\|\(m\.f=(\[.*?\])\)\)',
        text, _re.DOTALL
    )
    if _vite_arr:
        bare_chunks = _re.findall(r'["\']([A-Za-z0-9][^"\']+' + JS_CHUNK + r')["\']',
                                  _vite_arr.group(1))
        for bc in bare_chunks:
            if '/' not in bc:   # bare name → Vite puts these in /assets/
                parsed_base = urlparse(base_url)
                r = f"{parsed_base.scheme}://{parsed_base.netloc}/assets/{bc}"
                found.add(r)

    # ── Lazy-loaded JS chunks: import("./chunk-abc.js") ──────────────
    # Dynamic import() for Vite code-split chunks
    for m in _re.finditer(
        r'import\s*\(\s*["\'](\.[^"\']+' + JS_CHUNK + r')["\']',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── import/require with asset extensions ──────────────────────────
    for m in _re.finditer(
        r'(?:import|require)\s*\(\s*["\']([^"\']+' + ASSET_EXTS + r')["\']',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── Bare filenames (no path separator) → resolve only as written ────
    # Do not guess common folders such as images/, img/, or assets/. The
    # author may use any directory name; speculative guesses create 404s.
    # Howler.js loads audio relative to HTML root — bare MP3 names are
    # loaded from root or common music directories.
    _bare_re = _re.compile(
        r'["\`]([A-Za-z0-9][^"\`/\n\r<>{}()|\\]{0,200}' + ASSET_EXTS + r')["\`]',
        _re.IGNORECASE
    )
    for m in _bare_re.finditer(text):
        raw = m.group(1)
        if raw in inferred_dynamic_assets:
            continue
        if '/' not in raw:
            found.add(urljoin(base_url.rstrip('/') + '/', raw))

    # ── srcset (scan in ALL file types — JS template strings can carry it) ──
    for m in _re.finditer(r'srcset\s*=\s*["\']([^"\']+)["\']', text, _re.IGNORECASE):
        for entry in m.group(1).split(','):
            url_part = entry.strip().split()[0] if entry.strip() else ''
            if url_part:
                r = _resolve(url_part)
                if r: found.add(r)

    # ── HTML attributes: data-src, data-lazy, poster ──────────────────
    if file_ext in ('.html', '.htm', '.svg'):
        # data-src, data-lazy, data-background, poster
        for attr in ('data-src', 'data-lazy', 'data-background', 'data-poster',
                     'poster', 'data-original', 'data-bg'):
            for m in _re.finditer(
                attr + r'\s*=\s*["\']([^"\']+' + ASSET_EXTS + r')["\']',
                text, _re.IGNORECASE
            ):
                r = _resolve(m.group(1))
                if r: found.add(r)
        # <source src="..."> elements
        for m in _re.finditer(
            r'<source[^>]+src\s*=\s*["\']([^"\']+)["\']', text, _re.IGNORECASE
        ):
            r = _resolve(m.group(1))
            if r: found.add(r)
        # <link rel="preload" href="...">
        for m in _re.finditer(
            r'<link[^>]+rel\s*=\s*["\']preload["\'][^>]+href\s*=\s*["\']([^"\']+)["\']',
            text, _re.IGNORECASE
        ):
            r = _resolve(m.group(1))
            if r: found.add(r)

    # ── SVG <image href="..."> / <image xlink:href="..."> ─────────────
    if file_ext in ('.svg', '.html', '.htm'):
        for m in _re.finditer(
            r'<image[^>]+(?:href|xlink:href)\s*=\s*["\']([^"\']+)["\']',
            text, _re.IGNORECASE
        ):
            r = _resolve(m.group(1))
            if r: found.add(r)

    # ── Webpack/Vite manifest files ───────────────────────────────────
    # If this file looks like a build manifest, extract all asset paths.
    if file_ext == '.json':
        try:
            manifest = json.loads(text)
            if isinstance(manifest, dict):
                def _add_manifest_asset(raw_value: str) -> None:
                    """Add only a real asset token, never an HTML/text wrapper."""
                    value = str(raw_value or '').strip()
                    if not value or value.startswith(('data:', '#', 'javascript:')):
                        return

                    # JSON payloads can contain rich HTML descriptions. Extract
                    # their explicit resource attributes, but do not treat the
                    # entire description as a filename merely because it
                    # contains a substring ending in .png/.css/etc.
                    if '<' in value or '>' in value:
                        embedded = _re.findall(
                            r'(?:src|href)\s*=\s*["\']([^"\']+)["\']',
                            value,
                            _re.IGNORECASE,
                        )
                        embedded += _re.findall(
                            r'url\(\s*["\']?([^"\')\s]+)["\']?\s*\)',
                            value,
                            _re.IGNORECASE,
                        )
                        for candidate in embedded:
                            _add_manifest_asset(candidate)
                        return

                    asset_path = urlparse(value).path
                    if not _re.search(
                        r'\.(?:webp|avif|png|jpg|jpeg|gif|svg|ico|bmp|tiff|'
                        r'mp3|ogg|opus|wav|m4a|aac|flac|mp4|webm|mov|ogv|m4v|'
                        r'woff2?|ttf|otf|eot|js|mjs|css|json|wasm)$',
                        asset_path,
                        _re.IGNORECASE,
                    ):
                        return
                    resolved = _resolve(value)
                    if resolved:
                        found.add(resolved)

                def _walk_manifest(obj):
                    if isinstance(obj, str):
                        _add_manifest_asset(obj)
                    elif isinstance(obj, dict):
                        for v in obj.values():
                            _walk_manifest(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            _walk_manifest(v)
                _walk_manifest(manifest)
        except (json.JSONDecodeError, ValueError) as _ignored_exc:
            logger.debug("Ignored recoverable exception in _scan_file_for_assets (line 19015): %s", _ignored_exc)

    # ── Service Worker precache manifest ──────────────────────────────
    # Pattern: self.__precacheManifest = [{url: "...", revision: "..."}]
    if file_ext in ('.js', '.mjs', '.cjs'):
        for m in _re.finditer(
            r'__precacheManifest\s*=\s*(\[.*?\])',
            text, _re.DOTALL
        ):
            try:
                entries = json.loads(m.group(1))
                for entry in entries:
                    url_val = entry.get('url', '') if isinstance(entry, dict) else ''
                    if url_val:
                        r = _resolve(url_val)
                        if r: found.add(r)
            except (json.JSONDecodeError, ValueError, AttributeError) as _ignored_exc:
                logger.debug("Ignored recoverable exception in _scan_file_for_assets (line 19032): %s", _ignored_exc)

    # ── Deduplicate / validate ────────────────────────────────────────
    cleaned: Set[str] = set()
    for u in found:
        try:
            parsed = urlparse(u)
            if parsed.scheme in ('http', 'https') and parsed.netloc:
                cleaned.add(urlunparse(parsed._replace(fragment='')))
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _scan_file_for_assets (line 19042): %s", _ignored_exc)

    return cleaned


def _infer_dynamic_asset_paths(text: str) -> Dict[str, Set[str]]:
    """Map image-array tokens to paths formed with a JS base variable.

    Only literals inside explicitly image/asset-named arrays are considered.
    Pairing a base variable with every image-looking string in an entire
    application bundle produces nonsense combinations from UI suffixes and
    template fragments.
    """
    import re as _re

    prefixes = []
    assignment = _re.compile(
        r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*["\']([^"\']+/)["\']',
        _re.IGNORECASE,
    )
    for match in assignment.finditer(text or ""):
        if not _re.search(r"(?:img|image|asset)", match.group(1), _re.IGNORECASE):
            continue
        prefix = match.group(2).strip()
        if prefix and not prefix.startswith(("http://", "https://", "//", "data:")):
            prefixes.append(prefix)
    if not prefixes:
        return {}

    image_literal = _re.compile(
        r'["\']([^"\'\n\r<>{}()|\\]{1,300}\.(?:webp|avif|png|jpe?g|gif|svg|ico|bmp|tiff)(?:\?[^"\']*)?)["\']',
        _re.IGNORECASE,
    )
    asset_array = _re.compile(
        r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\[(.*?)\]\s*;?',
        _re.IGNORECASE | _re.DOTALL,
    )
    inferred: Dict[str, Set[str]] = {}
    for array_match in asset_array.finditer(text or ""):
        if not _re.search(r"(?:img|image|asset)", array_match.group(1), _re.IGNORECASE):
            continue
        for match in image_literal.finditer(array_match.group(2)):
            raw = match.group(1).strip()
            if not raw or raw.startswith(("/", "./", "../", "http://", "https://", "//", "data:")):
                continue
            for prefix in prefixes:
                if raw.startswith(prefix):
                    continue
                inferred.setdefault(raw, set()).add(prefix.rstrip("/") + "/" + raw.lstrip("/"))
    return inferred


__all__ = [
    "_is_probable_raw_cdn_asset", "_check_image_dedup",
    "_make_placeholder_svg", "_PLACEHOLDER_DATA_URI",
    "_safe_response_text", "_scan_file_for_assets", "_infer_dynamic_asset_paths",
]



def _deep_scan_project_assets(
    project_str: str,
    base_url: str,
) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Parse project.json as JSON and walk the entire object tree to find ALL
    image, audio, and YouTube URLs — including nested structures that the
    simple field-name regex cannot reach.

    Handles the ICC Plus v2.9.1 audio architecture:
      • soundEffects[].audio  → direct audio URL (not a top-level field)
      • bgmId + useAudioURL   → bgmId is a URL only when sibling useAudioURL=true;
                                 otherwise bgmId is a YouTube video ID

    Returns:
        (image_paths, audio_paths, youtube_ids)
        where each is a set of raw strings from the JSON (relative or absolute).
    """
    image_paths:   Set[str] = set()
    audio_paths:   Set[str] = set()
    youtube_ids:   Set[str] = set()

    image_keys = {f.lower() for f in IMAGE_FIELDS} | set(ICC_PLUS_IMAGE_KEYS)
    audio_keys = {f.lower() for f in AUDIO_FIELDS}

    def _is_data_uri(v: str) -> bool:
        return v.startswith("data:")

    def _looks_like_audio_url(v: str) -> bool:
        """True if value looks like a downloadable audio file URL/path."""
        if _YOUTUBE_URL_RE.search(v):
            return False   # YouTube URL handled separately
        path = urlparse(v).path.lower()
        ext = os.path.splitext(path)[1]
        return ext in AUDIO_EXTENSIONS or v.startswith(("http://", "https://")) and not _YOUTUBE_ID_RE.match(v)

    def _walk(obj, parent_key: str = "", siblings: Optional[Dict] = None) -> None:
        """Recursively walk any JSON value."""
        if obj is None:
            return
        if isinstance(obj, list):
            for item in obj:
                _walk(item)
            return
        if isinstance(obj, dict):
            use_audio_url = bool(obj.get("useAudioURL", False))

            for key, value in obj.items():
                key_lower = key.lower()

                if isinstance(value, str):
                    v = value.strip()
                    if not v or _is_data_uri(v):
                        continue

                    # ── Image fields ─────────────────────────────────────
                    if key_lower in image_keys:
                        image_paths.add(v)

                    # ── bgmId: context-dependent ──────────────────────────
                    elif key_lower == "bgmid":
                        if use_audio_url:
                            # bgmId is a direct audio URL
                            if _YOUTUBE_URL_RE.search(v):
                                youtube_ids.add(v)
                            elif _SOUNDCLOUD_URL_RE.search(v):
                                youtube_ids.add(v)   # yt-dlp handles SoundCloud too
                            else:
                                audio_paths.add(v)
                        else:
                            # bgmId is a YouTube video ID or SoundCloud URL
                            if _YOUTUBE_URL_RE.search(v) or _SOUNDCLOUD_URL_RE.search(v):
                                youtube_ids.add(v)
                            elif _YOUTUBE_ID_RE.match(v) and v:
                                youtube_ids.add(f"https://www.youtube.com/watch?v={v}")
                            # else: unknown format, skip

                    # ── Simple audio fields ───────────────────────────────
                    elif key_lower in audio_keys:
                        if _YOUTUBE_URL_RE.search(v) or _SOUNDCLOUD_URL_RE.search(v):
                            youtube_ids.add(v)
                        else:
                            audio_paths.add(v)

                    # ── Catch any remaining URL-looking values ─────────────
                    # (e.g. custom viewer fields not in our lists)
                    elif v.startswith(("http://", "https://")):
                        path_part = urlparse(v).path.lower()
                        ext = os.path.splitext(path_part)[1]
                        if ext in IMAGE_EXTENSIONS:
                            image_paths.add(v)
                        elif ext in AUDIO_EXTENSIONS:
                            audio_paths.add(v)

                    # ── Relative paths with asset extensions ──────────────
                    # Catch "images/hero.png", "audio/theme.mp3" etc.
                    # that aren't in known field lists but ARE valid paths.
                    elif '/' in v and not v.startswith(('#', 'javascript:')):
                        ext = os.path.splitext(v.split('?')[0])[1].lower()
                        if ext in IMAGE_EXTENSIONS:
                            image_paths.add(v)
                        elif ext in AUDIO_EXTENSIONS:
                            audio_paths.add(v)

                    # ── HTML <img> embedded in text/description fields ────
                    # CYOA creators put HTML in choice text, descriptions,
                    # titles, etc. with inline <img src="..."> tags.
                    _img_refs = re.findall(
                        r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']',
                        v, re.IGNORECASE
                    )
                    for img_url in _img_refs:
                        if img_url and not img_url.startswith('data:'):
                            image_paths.add(img_url)

                    # ── Markdown image syntax ─────────────────────────────
                    # ![alt text](image_url) — used in some custom viewers
                    for md_match in re.finditer(
                        r'!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|webp|gif|svg|avif))\)',
                        v, re.IGNORECASE
                    ):
                        image_paths.add(md_match.group(1))

                    # ── CSS url() in inline style values ──────────────────
                    for css_match in re.finditer(
                        r'url\(["\']?([^"\')\s]+\.(?:png|jpg|jpeg|webp|gif|svg|avif|mp3|ogg|wav|woff2?|ttf))["\']?\)',
                        v, re.IGNORECASE
                    ):
                        css_url = css_match.group(1)
                        if not css_url.startswith('data:'):
                            ext = os.path.splitext(css_url.split('?')[0])[1].lower()
                            if ext in IMAGE_EXTENSIONS:
                                image_paths.add(css_url)
                            elif ext in AUDIO_EXTENSIONS:
                                audio_paths.add(css_url)

                elif isinstance(value, list):
                    # ── bgmList / playlist: list of YouTube IDs or audio URLs ──
                    if key_lower in BGMLIST_FIELDS:
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                v2 = item.strip()
                                if _YOUTUBE_URL_RE.search(v2) or _SOUNDCLOUD_URL_RE.search(v2):
                                    youtube_ids.add(v2)
                                elif _YOUTUBE_ID_RE.match(v2):
                                    youtube_ids.add(f"https://www.youtube.com/watch?v={v2}")
                                elif any(v2.endswith(e) for e in AUDIO_EXTENSIONS):
                                    audio_paths.add(v2)
                            elif isinstance(item, dict):
                                _walk(item, parent_key=key)
                    else:
                        _walk(value, parent_key=key)

                elif isinstance(value, dict):
                    _walk(value, parent_key=key)
            return

        # scalar non-string — nothing to do
        return

    # Try JSON parse first for accuracy
    try:
        obj = json.loads(project_str)
        _walk(obj)
        logger.info(
            f"Deep JSON scan: {len(image_paths)} image(s), "
            f"{len(audio_paths)} direct audio file(s), "
            f"{len(youtube_ids)} YouTube reference(s)."
        )
        return image_paths, audio_paths, youtube_ids
    except (json.JSONDecodeError, ValueError) as _ignored_exc:
        logger.debug("Ignored recoverable exception in _deep_scan_project_assets (line 13265): %s", _ignored_exc)

    # Fallback: already-minified or slightly malformed JSON — use regex scan
    # (the existing process_images regex still handles this path)
    logger.debug("Deep JSON scan: JSON parse failed, falling back to regex scanner.")
    return image_paths, audio_paths, youtube_ids
