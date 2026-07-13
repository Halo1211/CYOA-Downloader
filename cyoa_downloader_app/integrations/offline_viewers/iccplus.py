"""ICC Plus/offline-viewer HTML injection helpers extracted from legacy.py."""

from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Any, Dict, Tuple

from ...logging_setup import logger

def _build_html_interceptor(data_js: str, size_bytes: int) -> str:
    """Build the HTML fetch/XHR interceptor script tag (fallback when JS embed fails)."""
    # Same "</script>"-termination hardening as the main
    # injection path, in case a future caller passes unescaped JSON.
    data_js = data_js.replace("</", "<\\/")
    return (
        f'<script id="__cyoa_offline_patch__">'
        f'(function(){{'
        f'var D={data_js};'
        f'var R=/project\\.json|data\\.json/i;'
        f'var _f=window.fetch;'
        f'window.fetch=function(u,o){{'
        f'if(R.test(String(u||"")))return Promise.resolve(new Response(JSON.stringify(D),'
        f'{{status:200,headers:{{"Content-Type":"application/json"}}}}));'
        f'return _f?_f.call(this,u,o):Promise.reject(new Error("fetch N/A"));'
        f'}};'
        f'window.__CYOA_OFFLINE__=true;window.__CYOA_DATA__=D;'
        f'document.addEventListener("DOMContentLoaded",function(){{'
        f'var el=document.getElementById("projectSize");'
        f'if(el)el.textContent="{size_bytes}";'
        f'}});'
        f'}})();'
        f'</script>\n'
    )


def _inject_into_head(html: str, script: str) -> str:
    """Insert script as first element inside <head>."""
    # Match <head ...attrs...> too (e.g. OpenGraph
    # prefix=), not just the literal "<head>"; \b guard keeps <header> out.
    m = re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE)
    if m:
        insert_at = m.end()
        return html[:insert_at] + "\n" + script + html[insert_at:]
    return script + "\n" + html


def _unique_folder(base: str) -> str:
    """Return *base* if it does not exist, else base_1, base_2, …"""
    if not os.path.exists(base):
        return base
    counter = 1
    while os.path.exists(f"{base}_{counter}"):
        counter += 1
    candidate = f"{base}_{counter}"
    logger.info(f"Output folder collision: {base!r} exists → using {candidate!r}")
    return candidate



def _html_escape(value: Any) -> str:
    """Minimal HTML attribute/text escaping for generated viewerConfig tags."""
    return (str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _extract_iccplus_app_and_viewer_config(project_json_str: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (app_obj, viewerConfig) from ICC Plus export variants."""
    try:
        root = json.loads(project_json_str) if project_json_str.strip().startswith("{") else {}
    except Exception:
        return {}, {}
    if not isinstance(root, dict):
        return {}, {}
    app = root.get("app") if isinstance(root.get("app"), dict) else root
    vc = root.get("viewerConfig") if isinstance(root.get("viewerConfig"), dict) else app.get("viewerConfig") if isinstance(app, dict) and isinstance(app.get("viewerConfig"), dict) else {}
    return app if isinstance(app, dict) else {}, vc if isinstance(vc, dict) else {}


def _apply_iccplus_viewer_config_to_html(
    html: str,
    project_json_str: str,
    site_folder: str,
    size_bytes: int,
    fallback_title: str,
) -> str:
    """Apply safe ICC Plus viewerConfig hints to offline viewer HTML/CSS."""
    app, vc = _extract_iccplus_app_and_viewer_config(project_json_str)
    if not vc:
        return html
    title = str(vc.get("title") or app.get("title") or app.get("name") or fallback_title or "CYOA").strip()
    favicon = str(vc.get("favicon") or app.get("favicon") or "").strip()
    loading_bg = str(vc.get("loadingBgImage") or vc.get("loadingImage") or "").strip()
    loading_text = str(vc.get("loadingText") or vc.get("loadingMessage") or "").strip()

    if title:
        if re.search(r"<title[^>]*>.*?</title>", html, flags=re.I | re.S):
            html = re.sub(r"<title[^>]*>.*?</title>", lambda _m: f"<title>{_html_escape(title)}</title>", html, count=1, flags=re.I | re.S)  # literal replacement — raw \g/\1/trailing backslash in project text crashed re.sub
        else:
            html = _inject_into_head(html, f"<title>{_html_escape(title)}</title>\n")

    if favicon:
        fav_tag = f'<link rel="icon" href="{_html_escape(favicon)}">'
        if re.search(r"<link[^>]+rel=[\"\'](?:icon|shortcut icon)[\"\'][^>]*>", html, flags=re.I):
            html = re.sub(r"<link[^>]+rel=[\"\'](?:icon|shortcut icon)[\"\'][^>]*>", lambda _m: fav_tag, html, count=1, flags=re.I)  # literal replacement — raw \g/\1/trailing backslash in project text crashed re.sub
        else:
            html = _inject_into_head(html, fav_tag + "\n")

    html = re.sub(r"(<[^>]+id=[\"\']projectSize[\"\'][^>]*>)\s*[^<]*", rf'\g<1>{size_bytes}', html, flags=re.I)
    if loading_text:
        html = re.sub(r"(<[^>]+id=[\"\']loadingText[\"\'][^>]*>)\s*[^<]*", lambda _m: _m.group(1) + _html_escape(loading_text), html, flags=re.I)  # literal replacement — raw \g/\1/trailing backslash in project text crashed re.sub

    if loading_bg or loading_text:
        css_dir = os.path.join(site_folder, "css")
        os.makedirs(css_dir, exist_ok=True)
        loading_css = os.path.join(css_dir, "loading.css")
        lines = ["/* Generated by CYOA Downloader: ICC Plus viewerConfig */"]
        # loadingText/loadingBg are project-controlled
        # text inserted into CSS string/url literals. Escaping only the quote
        # meant a trailing "\" produced "\'" (escaped close-quote → unterminated
        # string → rule dropped), and raw newlines are invalid inside CSS
        # strings. Same bug class as rev18 (raw project text into a structured
        # language). Backslash must be escaped FIRST, then quotes; newlines
        # collapse to spaces. For url(): percent-encode the CSS-significant
        # bytes instead (URLs may not contain literal \, ', or newlines anyway).
        def _css_str(_s: str) -> str:
            return (_s.replace("\\", "\\\\").replace("'", "\\'")
                      .replace("\r", " ").replace("\n", " "))
        def _css_url(_s: str) -> str:
            return (_s.replace("\\", "%5C").replace("'", "%27")
                      .replace("\r", "").replace("\n", "").replace(")", "%29"))
        if loading_bg:
            lines.append(":root{--cyoa-loading-bg:url('%s');}" % _css_url(loading_bg))
            lines.append("body:before{content:'';position:fixed;inset:0;background-image:var(--cyoa-loading-bg);background-size:cover;background-position:center;opacity:.18;pointer-events:none;z-index:0;}")
        if loading_text:
            lines.append("#loadingText::after{content:' %s';}" % _css_str(loading_text))
        try:
            pathlib.Path(loading_css).write_text("\n".join(lines) + "\n", encoding="utf-8")
            if "css/loading.css" not in html:
                html = _inject_into_head(html, '<link rel="stylesheet" href="css/loading.css">\n')
            logger.info("Applied ICC Plus viewerConfig: title/favicon/loading CSS")
        except Exception as e:
            logger.debug(f"Could not write ICC Plus loading.css: {e}")
    return html


__all__ = [
    "_build_html_interceptor", "_inject_into_head", "_unique_folder",
    "_html_escape", "_extract_iccplus_app_and_viewer_config",
    "_apply_iccplus_viewer_config_to_html",
]
