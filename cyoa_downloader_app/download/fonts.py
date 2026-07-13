"""Font discovery, analysis, download, and project JSON rewrite helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urljoin, urlparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - mirrors legacy dependency error
    def BeautifulSoup(*_args, **_kwargs):  # type: ignore
        raise RuntimeError(
            "Missing dependency: beautifulsoup4 is required for HTML/ICC parsing. "
            "Install it with: pip install beautifulsoup4"
        )

from ..constants.assets import FONT_EXTENSIONS
from ..core.atomic_io import atomic_write_bytes
from ..logging_setup import logger
from ..network.fetch import fetch_response
from ..project.discover import get_source


def _find_font_urls(
    project_str: str,
    base_url: str,
    html_source: str = "",
    extra_css_urls: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Return {font_url: description} for all fonts found in:
      1. project.json / project string (direct URLs + CSS url() + Google Fonts refs)
      2. viewer HTML source (Google Fonts <link>, local font <link>)
      3. any extra CSS URLs provided (e.g. from downloaded CSS files)

    Google Fonts CSS is resolved in parallel for speed.
    Duplicate font URLs are deduplicated automatically.
    """
    # Scan a slash-unescaped copy so JSON-escaped font URLs
    # ("https:\/\/cdn\/f.woff2") are discovered too (bug class rev6-rev8).
    # Scan-side only: the rewrite in _download_fonts_into_folder handles both
    # escaped and unescaped occurrences.
    if "\\/" in project_str:
        project_str = project_str.replace("\\/", "/")

    results: Dict[str, str] = {}
    gf_css_urls: Set[str] = set()   # Google Fonts CSS URLs to resolve
    raw_font_urls: List[Tuple[str, str]] = []  # (url, description)

    # ── 1. Scan project.json ─────────────────────────────────────────
    # Direct font file URLs
    for u in re.findall(
        r'https?://[^\s"\'<>]+\.(?:woff2?|ttf|otf|eot)[^\s"\'<>]*',
        project_str, re.IGNORECASE
    ):
        raw_font_urls.append((u, "project.json direct URL"))

    # Google Fonts CSS links in project.json
    for gf in re.findall(r'https://fonts\.googleapis\.com/css[^\s"\'<>]*', project_str):
        gf_css_urls.add(gf)

    # ICC Plus stores googleFonts as family names, customFonts as stylesheet URLs,
    # and customCSS as arbitrary CSS text. Parse JSON structurally so font assets
    # are found even when they are not literal font-file URLs.
    icc_css_urls: Set[str] = set()

    def _add_google_family(family_value: str) -> None:
        fam = str(family_value or '').strip()
        if not fam:
            return
        if fam.startswith(('http://', 'https://')):
            if 'fonts.googleapis.com' in fam:
                gf_css_urls.add(fam)
            else:
                icc_css_urls.add(urljoin(base_url.rstrip('/') + '/', fam))
            return
        fam_q = quote(fam.replace('+', ' '), safe='').replace('%20', '+')
        gf_css_urls.add(f'https://fonts.googleapis.com/css2?family={fam_q}:wght@400;600;700&display=swap')

    def _scan_css_text_for_fonts(css_text: str, source_url: str = '') -> None:
        for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', css_text or ''):
            if not fu or fu.startswith('data:'):
                continue
            clean = fu.split('?', 1)[0].lower()
            if any(clean.endswith(ext) for ext in FONT_EXTENSIONS):
                resolved = fu if fu.startswith(('http://', 'https://')) else urljoin(source_url or base_url, fu)
                raw_font_urls.append((resolved, 'ICC Plus customCSS url()'))
        for gf in re.findall(r'https://fonts\.googleapis\.com/css[^\s"\'<>]*', css_text or ''):
            gf_css_urls.add(gf)

    try:
        _font_obj = json.loads(project_str)
        def _walk_font_config(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    kl = str(k).lower()
                    if kl == 'googlefonts':
                        vals = v if isinstance(v, list) else [v]
                        for item in vals:
                            if isinstance(item, str):
                                _add_google_family(item)
                            elif isinstance(item, dict):
                                _add_google_family(item.get('family') or item.get('name') or item.get('fontFamily') or '')
                    elif kl == 'customfonts':
                        vals = v if isinstance(v, list) else [v]
                        for item in vals:
                            if isinstance(item, str):
                                u = item.strip()
                                if u:
                                    icc_css_urls.add(u if u.startswith(('http://','https://')) else urljoin(base_url.rstrip('/') + '/', u))
                            elif isinstance(item, dict):
                                u = item.get('url') or item.get('href') or item.get('src') or item.get('css') or ''
                                if u:
                                    icc_css_urls.add(u if str(u).startswith(('http://','https://')) else urljoin(base_url.rstrip('/') + '/', str(u)))
                    elif kl == 'customcss' and isinstance(v, str):
                        _scan_css_text_for_fonts(v, base_url)
                    _walk_font_config(v)
            elif isinstance(node, list):
                for item in node:
                    _walk_font_config(item)
        _walk_font_config(_font_obj)
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _find_font_urls (line 14469): %s", _ignored_exc)

    for css_url in sorted(icc_css_urls):
        try:
            css_text = get_source(css_url, extra_headers={"User-Agent": "Mozilla/5.0"}) or ""
            _scan_css_text_for_fonts(css_text, css_url)
        except Exception as e:
            logger.debug(f"ICC Plus custom font stylesheet scan failed: {css_url} — {e}")

    # CSS url() references in project.json
    for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', project_str):
        if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS) and not fu.startswith("data:"):
            url = fu if fu.startswith("http") else urljoin(base_url.rstrip("/") + "/", fu)
            raw_font_urls.append((url, "project.json CSS url()"))

    # ── 2. Scan viewer HTML ──────────────────────────────────────────
    if html_source:
        soup_h = BeautifulSoup(html_source, "html.parser")

        # <link rel="stylesheet" href="https://fonts.googleapis.com/...">
        for tag in soup_h.find_all("link", rel=lambda r: r and "stylesheet" in (r if isinstance(r, str) else " ".join(r)).lower()):
            href = tag.get("href", "")
            if "fonts.googleapis.com" in href:
                gf_css_urls.add(href)
            elif any(href.lower().endswith(ext) for ext in FONT_EXTENSIONS):
                url = href if href.startswith("http") else urljoin(base_url, href)
                raw_font_urls.append((url, "index.html <link>"))

        # Inline <style> blocks
        for style_tag in soup_h.find_all("style"):
            css_text = style_tag.string or ""
            for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', css_text):
                if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS) and not fu.startswith("data:"):
                    url = fu if fu.startswith("http") else urljoin(base_url, fu)
                    raw_font_urls.append((url, "index.html inline <style>"))

    # ── 3. Scan extra CSS files ──────────────────────────────────────
    for css_url in (extra_css_urls or []):
        css_text = get_source(css_url) or ""
        for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', css_text):
            if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS) and not fu.startswith("data:"):
                resolved = fu if fu.startswith("http") else urljoin(css_url, fu)
                raw_font_urls.append((resolved, f"CSS: {css_url}"))
        for gf in re.findall(r'https://fonts\.googleapis\.com/css[^\s"\'<>]*', css_text):
            gf_css_urls.add(gf)

    # ── 4. Resolve Google Fonts CSS in parallel ──────────────────────
    def _resolve_gf_css(gf_url: str) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        logger.info(f"  Resolving Google Fonts: {gf_url}")
        css = get_source(gf_url, extra_headers={"User-Agent": "Mozilla/5.0"})
        if css:
            for fu in re.findall(r'url\(([^)]+)\)', css):
                fu = fu.strip("\"'")
                if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS):
                    found.append((fu, f"Google Fonts ({gf_url})"))
        return found

    if gf_css_urls:
        logger.info(f"Resolving {len(gf_css_urls)} Google Fonts CSS URL(s) in parallel…")
        with ThreadPoolExecutor(max_workers=min(len(gf_css_urls), 8)) as ex:
            for batch in ex.map(_resolve_gf_css, sorted(gf_css_urls)):
                raw_font_urls.extend(batch)

    # ── 5. Deduplicate ───────────────────────────────────────────────
    seen_urls: Set[str] = set()
    for url, desc in raw_font_urls:
        url = url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results[url] = desc

    return results


def analyse_fonts(project_str: str, base_url: str, html_source: str = "") -> None:
    fonts = _find_font_urls(project_str, base_url, html_source=html_source)
    if not fonts:
        logger.info("Font analysis: no external fonts found.")
        return
    logger.info(f"Font analysis: {len(fonts)} font file(s) found:")
    for url, source in fonts.items():
        logger.info(f"  [{source}]  {url}")


def _download_fonts_into_folder(
    project_str: str,
    base_url: str,
    folder: str,
    html_source: str = "",
    skip_if_website_mode: bool = False,
) -> str:
    """
    Download all fonts → <folder>/fonts/, rewrite project_str to use local paths.

    In ICC mode (skip_if_website_mode=True), WebsiteDownloader already handles
    fonts referenced in CSS/HTML — only fonts found exclusively in project.json
    are downloaded here to avoid double-downloading.
    """
    fonts = _find_font_urls(project_str, base_url, html_source=html_source)
    if not fonts:
        logger.info("No fonts to download.")
        return project_str

    fonts_dir = os.path.join(folder, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    logger.info(f"Downloading {len(fonts)} font(s)…")

    # Track saved filenames to avoid collisions
    saved_names: Dict[str, str] = {}   # basename → full path
    url_to_local: Dict[str, str] = {}

    def _download_one_font(item: Tuple[str, str]) -> Tuple[str, Optional[bytes]]:
        font_url, source = item
        try:
            r = fetch_response(
                font_url, timeout=20,
                extra_headers={"User-Agent": "Mozilla/5.0"},
                as_bytes=True, return_error_response=True,
            )
            if r is None:
                raise RuntimeError("request failed")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            raw_fn = os.path.basename(urlparse(font_url).path)
            if not raw_fn:
                raw_fn = hashlib.md5(font_url.encode()).hexdigest()[:8] + ".woff2"
            return font_url, r.content
        except Exception as e:
            logger.error(f"  Font failed: {font_url} — {e}")
            return font_url, None

    # Download in parallel
    with ThreadPoolExecutor(max_workers=min(len(fonts), 6)) as ex:
        for font_url, content in ex.map(_download_one_font, fonts.items()):
            if content is None:
                continue
            raw_fn = os.path.basename(urlparse(font_url).path) or hashlib.md5(font_url.encode()).hexdigest()[:8] + ".woff2"
            # Deduplicate filename
            base_fn, ext_fn = os.path.splitext(raw_fn)
            fn = raw_fn
            counter = 1
            while fn in saved_names and saved_names[fn] != os.path.join(fonts_dir, fn):
                fn = f"{base_fn}_{counter}{ext_fn}"
                counter += 1
            save_path = os.path.join(fonts_dir, fn)
            # Fix: skip if WebsiteDownloader already saved this font
            if os.path.exists(save_path):
                logger.debug(f"  Font already exists (skipping re-download): {fn}")
                saved_names[fn] = save_path
                url_to_local[font_url] = f"fonts/{fn}"
                continue
            atomic_write_bytes(save_path, content)
            saved_names[fn] = save_path
            url_to_local[font_url] = f"fonts/{fn}"
            logger.info(f"  Saved font: {fn}  ({fonts[font_url]})")

    # Rewrite project_str
    for orig, local in url_to_local.items():
        project_str = project_str.replace(orig, local)
        # Also rewrite the JSON-escaped occurrence
        # ("https:\/\/cdn\/f.woff2") that _find_font_urls now discovers via its
        # unescaped form; plain replace() alone would miss it. Forward slashes
        # need no escaping in JSON, so the plain local path stays valid.
        esc = orig.replace("/", "\\/")
        if esc != orig and esc in project_str:
            project_str = project_str.replace(esc, local)

    return project_str

__all__ = ["_find_font_urls", "analyse_fonts", "_download_fonts_into_folder"]
