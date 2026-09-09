"""Offline viewer project-data injector.

Phase 34 moves the large viewer-injection implementation out of ``legacy.py``
while preserving the historical strategies and output layout.
"""

from __future__ import annotations

import io
import hashlib
import json
import os
import pathlib
import re
from typing import Dict, Optional
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from ...core.archive import validate_zip_archive
from ...core.atomic_io import atomic_write_bytes, atomic_write_text
from ...core.paths import _copytree_merge_safe, _safe_archive_join
from ...diagnostics.reports import write_asset_failure_summary
from ...logging_setup import logger
from ...project.parse import extract_balanced_brace_block
from ..ai_core import _ssrf_block_cross_origin
from .iccplus import (
    _apply_iccplus_viewer_config_to_html,
    _build_html_interceptor,
    _html_escape,
    _inject_into_head,
    _unique_folder,
)
from .registry import (
    _ICC_MARKER_RE,
    _VIEWERS_DIR,
    _safe_viewer_archive_name,
    _safe_viewer_relative_path,
)


def _localize_preserved_index_assets(
    html: str,
    source_url: str,
    site_folder: str,
    *,
    fetcher=None,
) -> str:
    """Download preserved HTML assets that would break under ``file://``.

    Viewer-owned files already present in ``site_folder`` remain unchanged.
    Missing publisher/platform scripts, styles, and media are copied below a
    host-namespaced ``__source_assets__`` directory and their HTML references
    are rewritten. Failed downloads retain the original reference instead of
    deleting publisher markup.
    """
    if not html:
        return html
    if fetcher is None:
        from ...network.fetch import fetch_response

        fetcher = fetch_response

    root = os.path.abspath(site_folder)
    downloaded: dict[str, tuple[str, str]] = {}
    processing: set[str] = set()
    failed: dict[str, dict[str, str]] = {}
    css_url_re = re.compile(r"url\(\s*([^)]*?)\s*\)", re.IGNORECASE)
    css_import_re = re.compile(
        r"@import\s+(?:url\(\s*)?([\"']?)([^\"')\s;]+)\1\s*\)?",
        re.IGNORECASE,
    )
    quoted_asset_re = re.compile(
        r"(?P<quote>[\"'])(?P<url>[^\"'\r\n]{1,300}\.(?:js|mjs|css|json|png|jpe?g|gif|webp|svg|avif|woff2?|ttf|otf|mp3|ogg|wav)(?:\?[^\"'\s<>]*)?)(?P=quote)",
        re.IGNORECASE,
    )

    def _safe_existing(reference: str, owner_path: str) -> Optional[str]:
        parsed_ref = urlsplit(reference)
        if parsed_ref.scheme or parsed_ref.netloc:
            return None
        clean = unquote(parsed_ref.path or "")
        candidates = []
        if clean.startswith("/"):
            candidates.append(os.path.join(root, clean.lstrip("/\\")))
        else:
            candidates.extend((
                os.path.join(os.path.dirname(owner_path), clean),
                os.path.join(root, clean.lstrip("./\\")),
            ))
        for candidate in candidates:
            candidate = os.path.abspath(os.path.normpath(candidate))
            try:
                inside = os.path.commonpath([root, candidate]) == root
            except ValueError:
                inside = False
            if inside and os.path.isfile(candidate):
                return candidate
        return None

    def _kind_suffix(kind: str, content_type: str = "") -> str:
        if kind == "js" or "javascript" in content_type:
            return ".js"
        if kind == "css" or "text/css" in content_type:
            return ".css"
        return ""

    def _relative_reference(owner_path: str, target_path: str) -> str:
        return os.path.relpath(target_path, os.path.dirname(owner_path)).replace("\\", "/")

    def _process_css(text: str, css_url: str, css_path: str) -> str:
        def localize_css_ref(reference: str, kind: str = "") -> Optional[str]:
            value = reference.strip().strip("\"'")
            if not value or value.lower().startswith(("data:", "blob:", "#")):
                return None
            existing = _safe_existing(value, css_path)
            if existing:
                return _relative_reference(css_path, existing)
            target = _download(urljoin(css_url, value), kind)
            return _relative_reference(css_path, target[1]) if target else None

        def replace_import(match: re.Match[str]) -> str:
            local = localize_css_ref(match.group(2), "css")
            return f'@import url("{local}")' if local else match.group(0)

        def replace_url(match: re.Match[str]) -> str:
            local = localize_css_ref(match.group(1))
            return f'url("{local}")' if local else match.group(0)

        return css_url_re.sub(replace_url, css_import_re.sub(replace_import, text))

    def _process_js(text: str, js_url: str, js_path: str) -> str:
        count = 0

        def replace_asset(match: re.Match[str]) -> str:
            nonlocal count
            if count >= 500:
                return match.group(0)
            reference = match.group("url")
            existing = _safe_existing(reference, js_path)
            if existing:
                return match.group(0)
            count += 1
            kind = pathlib.PurePosixPath(urlsplit(reference).path).suffix.lower().lstrip(".")
            if kind in {"mjs", "js"}:
                kind = "js"
            elif kind == "css":
                kind = "css"
            else:
                kind = ""
            target = _download(urljoin(js_url, reference), kind)
            if not target:
                return match.group(0)
            local = _relative_reference(js_path, target[1])
            return f'{match.group("quote")}{local}{match.group("quote")}'

        return quoted_asset_re.sub(replace_asset, text)

    def _download(absolute_url: str, kind: str = "") -> Optional[tuple[str, str]]:
        parsed = urlsplit(absolute_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if _ssrf_block_cross_origin(absolute_url, source_url):
            error = "blocked: cross-origin internal host"
            failed.setdefault(
                absolute_url,
                {"url": absolute_url, "error": error, "kind": kind or "asset"},
            )
            logger.warning("Could not localize preserved HTML asset %s: %s", absolute_url, error)
            return None
        cache_key = absolute_url
        if cache_key in downloaded:
            return downloaded[cache_key]
        if cache_key in processing:
            return None
        raw_path = unquote(parsed.path or "").strip("/")
        path_parts = [part for part in raw_path.replace("\\", "/").split("/") if part]
        if any(part in {".", ".."} for part in path_parts):
            return None
        if not path_parts:
            path_parts = ["index"]
        safe_parts = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part) for part in path_parts]
        suffix = pathlib.PurePosixPath(safe_parts[-1]).suffix
        if not suffix:
            safe_parts[-1] += _kind_suffix(kind)
        if parsed.query:
            stem, ext = os.path.splitext(safe_parts[-1])
            safe_parts[-1] = f"{stem}_{hashlib.sha1(parsed.query.encode()).hexdigest()[:10]}{ext}"
        safe_host = re.sub(r"[^A-Za-z0-9._-]", "_", parsed.hostname)
        relative = pathlib.PurePosixPath("__source_assets__", safe_host, *safe_parts)
        destination = os.path.abspath(os.path.join(root, *relative.parts))
        try:
            if os.path.commonpath([root, destination]) != root:
                return None
        except ValueError:
            return None

        response = None
        processing.add(cache_key)
        try:
            response = fetcher(
                absolute_url,
                timeout=20,
                extra_headers={"User-Agent": "CYOA-Downloader"},
            )
            if response is None:
                failed.setdefault(
                    absolute_url,
                    {"url": absolute_url, "error": "request failed", "kind": kind or "asset"},
                )
                return None
            status = int(getattr(response, "status_code", 0) or 0)
            if status != 200:
                failed.setdefault(
                    absolute_url,
                    {
                        "url": absolute_url,
                        "error": f"HTTP {status}" if status else "request failed",
                        "kind": kind or "asset",
                    },
                )
                return None
            content = bytes(getattr(response, "content", b""))
            if not content:
                failed.setdefault(
                    absolute_url,
                    {"url": absolute_url, "error": "empty response", "kind": kind or "asset"},
                )
                return None
            if len(content) > 256 * 1024 * 1024:
                failed.setdefault(
                    absolute_url,
                    {
                        "url": absolute_url,
                        "error": "asset exceeds 256 MiB localization limit",
                        "kind": kind or "asset",
                    },
                )
                return None
            content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
            inferred_suffix = _kind_suffix(kind, content_type)
            if not pathlib.PurePosixPath(destination).suffix and inferred_suffix:
                destination += inferred_suffix
                relative = pathlib.PurePosixPath(relative.as_posix() + inferred_suffix)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            local_reference = "./" + relative.as_posix()
            downloaded[cache_key] = (local_reference, destination)
            if kind == "css" or "text/css" in content_type or destination.lower().endswith(".css"):
                decoded = content.decode("utf-8", errors="replace")
                atomic_write_text(destination, _process_css(decoded, absolute_url, destination))
            elif kind == "js" or "javascript" in content_type or destination.lower().endswith((".js", ".mjs")):
                decoded = content.decode("utf-8", errors="replace")
                atomic_write_text(destination, _process_js(decoded, absolute_url, destination))
            else:
                atomic_write_bytes(destination, content)
            logger.info("Localized preserved HTML asset: %s", local_reference)
            return downloaded[cache_key]
        except (OSError, TypeError, ValueError, KeyError) as exc:
            downloaded.pop(cache_key, None)
            failed.setdefault(
                absolute_url,
                {"url": absolute_url, "error": str(exc), "kind": kind or "asset"},
            )
            logger.warning("Could not localize preserved HTML asset %s: %s", absolute_url, exc)
            return None
        finally:
            processing.discard(cache_key)
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    soup = BeautifulSoup(html, "html.parser")
    for script in list(soup.find_all("script")):
        script_text = script.get_text(" ", strip=False).lower()
        src = str(script.get("src") or "").lower()
        if (
            script.has_attr("data-cf-beacon")
            or "cloudflareinsights.com" in src
            or "/cdn-cgi/" in src
            or "/cdn-cgi/" in script_text
        ):
            script.decompose()

    index_path = os.path.join(root, "index.html")
    attr_by_tag = {
        "script": ("src", "js"), "link": ("href", ""), "img": ("src", ""),
        "source": ("src", ""), "video": ("src", ""), "audio": ("src", ""),
    }
    for tag in soup.find_all(attr_by_tag.keys()):
        attr, kind = attr_by_tag[tag.name]
        reference = str(tag.get(attr) or "").strip()
        if not reference or reference.lower().startswith(
            ("#", "data:", "blob:", "javascript:", "mailto:", "tel:")
        ):
            continue
        if tag.name == "link" and "stylesheet" in {
            str(item).lower() for item in (tag.get("rel") or [])
        }:
            kind = "css"
        existing = _safe_existing(reference, index_path)
        if existing:
            if kind == "css":
                css_text = pathlib.Path(existing).read_text(encoding="utf-8", errors="replace")
                css_url = urljoin(source_url, reference)
                atomic_write_text(existing, _process_css(css_text, css_url, existing))
            elif kind == "js":
                js_text = pathlib.Path(existing).read_text(encoding="utf-8", errors="replace")
                js_url = urljoin(source_url, reference)
                atomic_write_text(existing, _process_js(js_text, js_url, existing))
            if tag.has_attr("integrity"):
                del tag["integrity"]
            if tag.has_attr("crossorigin"):
                del tag["crossorigin"]
            continue
        target = _download(urljoin(source_url, reference), kind)
        if target:
            tag[attr] = target[0]
            if tag.has_attr("integrity"):
                del tag["integrity"]
            if tag.has_attr("crossorigin"):
                del tag["crossorigin"]

    for style in soup.find_all("style"):
        original = style.string if style.string is not None else style.get_text("", strip=False)
        style.clear()
        style.append(_process_css(str(original), source_url, index_path))
    for tag in soup.find_all(style=True):
        tag["style"] = _process_css(str(tag.get("style") or ""), source_url, index_path)
    if failed:
        write_asset_failure_summary(
            list(failed.values()),
            root,
            source_url=source_url,
            title="Preserved Viewer Asset Download Failures",
        )
    return str(soup)


def _inject_project_font_links(html: str, project: object) -> str:
    """Materialize project-declared fonts and block duplicate online loads.

    ICC runtimes create Google/custom font ``link`` nodes after startup. The
    static links added here let the normal asset localizer archive the CSS and
    font files, while the guard suppresses only the duplicate remote font link.
    Project data itself remains byte-for-byte unchanged.
    """
    if not isinstance(project, dict):
        return html
    app = project.get("app") if isinstance(project.get("app"), dict) else project
    google_fonts = app.get("googleFonts") if isinstance(app, dict) else []
    custom_fonts = app.get("customFonts") if isinstance(app, dict) else []
    google = []
    for item in google_fonts if isinstance(google_fonts, list) else []:
        name = str(item or "").strip()
        if name and name not in google and len(name) <= 160:
            google.append(name)
    custom = []
    for item in custom_fonts if isinstance(custom_fonts, list) else []:
        reference = str(item or "").strip()
        if reference and reference not in custom and len(reference) <= 2048:
            custom.append(reference)
    if not google and not custom:
        return html

    links = []
    if google:
        families = "&amp;".join(
            "family=" + re.sub(r"\s+", "+", _html_escape(name)) for name in google
        )
        links.append(
            '<link rel="stylesheet" data-cyoa-offline-project-font '
            f'href="https://fonts.googleapis.com/css2?{families}&amp;display=swap">'
        )
    for reference in custom:
        links.append(
            '<link rel="stylesheet" data-cyoa-offline-project-font '
            f'href="{_html_escape(reference)}">'
        )

    custom_json = json.dumps(custom, ensure_ascii=False).replace("</", "<\\/")
    guard = (
        '<script data-cyoa-offline-font-guard>(function(){'
        f'var C={custom_json},O=HTMLHeadElement.prototype.appendChild;'
        'function A(u){try{return new URL(u,location.href).href}catch(e){return String(u||"")}}'
        'var S=new Set(C.map(A));HTMLHeadElement.prototype.appendChild=function(n){'
        'var h=n&&n.tagName==="LINK"?A(n.href):"";'
        'if(h&&(h.indexOf("https://fonts.googleapis.com/")===0||S.has(h)))return n;'
        'return O.call(this,n)};})();</script>'
    )
    return _inject_into_head(html, guard + "\n" + "\n".join(links))

def _apply_offline_viewer(
    output_dir: str,
    project_json_str: str,
    viewer_meta: Dict,
    file_name: str = "project",
    asset_source_dirs: Optional[Dict[str, str]] = None,
    source_html: str = "",
    source_url: str = "",
) -> Optional[str]:
    """
    Extract an offline viewer ZIP into output_dir and inject project data.

    Supports three injection strategies auto-detected from index.html/JS
    (tried in order; first match wins):
      A) Template markers (ICC_Remix): replaces {{ICC_PROJECT_DATA_SCRIPT}},
         {{ICC_PROJECT_SIZE}}, {{ICC_SITE_TITLE}}, {{ICC_FAVICON_TAG}}
      B) ICC Plus marker injection (ICC_Plus v1.x/v2.x): splices project data
         into the "{DEFAULT_STATE}" marker in app.js via balanced-brace match
      C) fetch() patch (New_Viewer, Viewer_1.8, fallbacks): writes project.json
         beside the entry point and rewrites fetch("project.json") calls. If no
         literal fetch() pattern matches, a window.fetch (fetch-only) interceptor
         is injected as the first <head> script so the CYOA works on file://
         without a local server.

    Returns path to index.html on success, None on failure.

    Docstring corrected: was "two strategies" and
    described B as a "window.fetch / XHR" interceptor. There are three
    strategies; B is marker injection, and the interceptor lives in C and
    overrides fetch only (no XHR) â€” see deferred note in the handoff.
    """
    import zipfile as _zf, shutil

    zip_filename = _safe_viewer_archive_name(viewer_meta.get("zip_filename", ""))
    entry_point = _safe_viewer_relative_path(
        viewer_meta.get("entry_point"), default="index.html"
    )
    raw_inner_archive = str(viewer_meta.get("inner_archive", "") or "").strip()
    inner_archive = (
        _safe_viewer_relative_path(raw_inner_archive)
        if raw_inner_archive
        else ""
    )
    if (
        not zip_filename
        or not entry_point
        or (raw_inner_archive and (not inner_archive or not inner_archive.lower().endswith(".zip")))
    ):
        logger.error("Offline viewer metadata contains an unsafe archive or entry path")
        return None
    zip_path = os.path.join(_VIEWERS_DIR, zip_filename)
    is_rar       = zip_path.lower().endswith(".rar")

    if is_rar:
        if inner_archive:
            logger.error("Nested viewer archives are supported only inside ZIP packages")
            return None
        try:
            import rarfile as _rf
        except ImportError:
            logger.error("rarfile package required for .rar viewers: pip install rarfile")
            return None

    if not os.path.exists(zip_path):
        logger.error(f"Offline viewer ZIP not found: {zip_path}")
        return None

    try:
        parsed_project = json.loads(project_json_str) if project_json_str.strip().startswith("{") else {}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.error(f"Cannot inject invalid project JSON: {exc}")
        return None
    if not isinstance(parsed_project, dict):
        parsed_project = {}

    # â”€â”€ Extract viewer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    site_folder = _unique_folder(os.path.join(output_dir, file_name + "_offline"))
    os.makedirs(site_folder, exist_ok=True)

    try:
        if is_rar:
            arc = _rf.RarFile(zip_path)
        else:
            # Revalidate at use time. A registered archive can be replaced on
            # disk later, so registration-time validation alone is not a safe
            # decompression boundary.
            validate_zip_archive(
                zip_path,
                max_members=10000,
                max_member_size=1024 * 1024 * 1024,
                max_total_size=4 * 1024 * 1024 * 1024,
                max_ratio=250.0,
            )
            if inner_archive:
                with _zf.ZipFile(zip_path) as outer_arc:
                    if inner_archive not in outer_arc.namelist():
                        raise ValueError(
                            f"Nested viewer archive not found: {inner_archive}"
                        )
                    nested_info = outer_arc.getinfo(inner_archive)
                    if nested_info.file_size > 1024 * 1024 * 1024:
                        raise ValueError("Nested viewer archive is too large")
                    nested_bytes = outer_arc.read(inner_archive)
                validate_zip_archive(
                    nested_bytes,
                    max_members=10000,
                    max_member_size=1024 * 1024 * 1024,
                    max_total_size=4 * 1024 * 1024 * 1024,
                    max_ratio=250.0,
                )
                arc = _zf.ZipFile(io.BytesIO(nested_bytes))
            else:
                arc = _zf.ZipFile(zip_path)

        with arc:
            members = arc.namelist()
            if len(members) > 10000:
                raise ValueError("Viewer archive contains too many members")
            # Detect if all files are under a single root folder (e.g. "Viewer 1.8/")
            roots = set(m.split("/")[0] for m in members if m.strip("/"))
            strip_prefix = ""
            if len(roots) == 1:
                root_dir = next(iter(roots))
                if all(m.startswith(root_dir + "/") or m == root_dir + "/" for m in members):
                    strip_prefix = root_dir + "/"

            # v7.5.6 hardening: decompression budget (zip-bomb guard).
            # Generous limits so no legitimate viewer package is affected:
            # 1 GB per member, 4 GB total.
            _MAX_MEMBER_BYTES = 1024 * 1024 * 1024
            _MAX_TOTAL_BYTES  = 4 * 1024 * 1024 * 1024
            _total_written = 0
            for member in members:
                target_rel = member[len(strip_prefix):] if strip_prefix else member
                if not target_rel or target_rel.endswith("/"):
                    continue
                target_path = _safe_archive_join(site_folder, target_rel)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                # Check the DECLARED uncompressed size before
                # arc.read() decompresses the member fully into RAM. Without this
                # a single crafted member that inflates to many GB would exhaust
                # memory *before* the post-read len(data) check could fire. The
                # post-read check below stays as defense-in-depth (a header can
                # under-declare its real size).
                try:
                    _declared = int(getattr(arc.getinfo(member), "file_size", 0) or 0)
                except Exception:
                    _declared = 0
                if _declared > _MAX_MEMBER_BYTES:
                    raise ValueError(f"Viewer archive member too large (declared): {member} ({_declared} bytes)")
                if _total_written + _declared > _MAX_TOTAL_BYTES:
                    raise ValueError("Viewer archive exceeds total decompression budget (4 GB)")
                data = arc.read(member)
                if len(data) > _MAX_MEMBER_BYTES:
                    raise ValueError(f"Viewer archive member too large: {member} ({len(data)} bytes)")
                _total_written += len(data)
                if _total_written > _MAX_TOTAL_BYTES:
                    raise ValueError("Viewer archive exceeds total decompression budget (4 GB)")
                atomic_write_bytes(target_path, data)

        logger.info(f"Offline viewer extracted: {site_folder}/")
    except Exception as e:
        logger.error(f"Failed to extract viewer: {e}")
        shutil.rmtree(site_folder, ignore_errors=True)
        return None

    # â”€â”€ Read index.html â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    index_path = _safe_archive_join(site_folder, entry_point)
    if not os.path.exists(index_path):
        # Try one level deep
        for root, _, files in os.walk(site_folder):
            if entry_point in files:
                index_path = os.path.join(root, entry_point)
                break

    if not os.path.exists(index_path):
        logger.error(f"entry_point '{entry_point}' not found in extracted viewer")
        shutil.rmtree(site_folder, ignore_errors=True)
        return None

    try:
        html = pathlib.Path(index_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Cannot read index.html: {e}")
        shutil.rmtree(site_folder, ignore_errors=True)
        return None

    # Parse metadata from project JSON
    size_bytes = len(project_json_str.encode("utf-8"))
    try:
        proj_obj = parsed_project
        proj_title = (
            proj_obj.get("app", proj_obj).get("title") or
            proj_obj.get("app", proj_obj).get("name") or
            file_name
        )
    except Exception:
        proj_title = file_name

    data_js = json.dumps(parsed_project, ensure_ascii=False, separators=(",", ":"))
    # data_js is embedded in inline <script> blocks below.
    # The HTML parser ends a script element at the first "</script" â€” even
    # inside a JS string â€” so any project whose text contains "</script>"
    # (row descriptions with raw HTML are common) truncated the script and
    # broke the offline viewer. "<\/" is a valid, semantically identical JSON
    # escape, so this is lossless for JSON.parse and JS literals alike.
    data_js = data_js.replace("</", "<\\/")

    # â”€â”€ Strategy A: Template markers (ICC_Remix style) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if "{{ICC_PROJECT_DATA_SCRIPT}}" in html:
        logger.info("Offline inject: using template strategy (ICC_Remix)")
        # ICC Remix startup reads: window.__CYOA_PROJECT__
        # (also set legacy names for compatibility)
        data_script = (
            f'<script id="__icc_offline_data__">'
            f'window.__CYOA_PROJECT__={data_js};'
            f'window.__ICCPLUS_DATA__={data_js};'
            f'window.__CYOA_DATA__={data_js};'
            f'</script>'
        )
        html = html.replace("{{ICC_PROJECT_DATA_SCRIPT}}", data_script)
        html = html.replace("{{ICC_PROJECT_SIZE}}", str(size_bytes))
        # Titles are project-controlled text; raw
        # insertion garbled the page when the title contained &, <, or >.
        # The strategy-B path already escapes via _html_escape â€” align this
        # template-marker path with it.
        html = html.replace("{{ICC_SITE_TITLE}}", _html_escape(proj_title))
        html = html.replace("{{ICC_FAVICON_TAG}}", "")
        # Colocate the fallback project.json with the
        # RESOLVED index (which may sit one level deep in multi-root viewer
        # ZIPs); writing to site_folder root broke relative fetch("project.json").
        # Also write physical project.json for fallback
        atomic_write_text(
            os.path.join(os.path.dirname(index_path), "project.json"),
            project_json_str,
        )

    # â”€â”€ Strategy B: ICC Plus marker injection (version-agnostic) â”€â”€â”€â”€â”€â”€
    # ICC Plus has a documented injection point in app.js:
    #   /*! Delete and replace this part with your project if you're pasting it in... */
    #   {DEFAULT_STATE}
    # This marker exists in ALL ICC Plus versions â€” v1.x, v2.x, and future.
    # We replace {DEFAULT_STATE} with the actual project data JSON.
    else:
        # Scan all JS files for ICC Plus marker
        icc_marker_file = None
        icc_marker_js   = None
        for _root, _, _files in os.walk(site_folder):
            for _fname in _files:
                if not _fname.endswith(".js"):
                    continue
                _fp = os.path.join(_root, _fname)
                try:
                    _jt = pathlib.Path(_fp).read_text(encoding="utf-8", errors="replace")
                    if _ICC_MARKER_RE.search(_jt):
                        icc_marker_file = _fp
                        icc_marker_js   = _jt
                        break
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _apply_offline_viewer (line 2956): %s", _ignored_exc)
            if icc_marker_file:
                break

        if icc_marker_file:
            logger.info(
                f"Offline inject: Strategy B - ICC Plus marker injection "
                f"({os.path.basename(icc_marker_file)})"
            )
            try:
                _proj = parsed_project
                # ICC Plus marker injection point receives the full project state:
                # - v1.18 (app.c533aa25.js): state.app = full flat JSON
                # - v2 (app.B6d7tc9y.js):   state.app = full flat JSON
                # Both read the WHOLE project JSON (rows, backpack, app flags all at root)
                # We inject the full root object, NOT just proj["app"],
                # because "app" sub-key only exists in some export formats.
                _app_js = json.dumps(_proj, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                _app_js = data_js

            # â”€â”€ Balanced-brace injection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # Find the exact {DEFAULT_STATE} after the marker using brace counting
            # â€” far more reliable than a regex on minified JS with nested objects.
            _MARKER_SEARCH = re.compile(
                r'(/\*!\s*Delete and replace this part[^*]*\*/\s*'
                r'|//\s*Delete and replace this part[^\n]*\n)',
                re.DOTALL | re.IGNORECASE
            )
            _m = _MARKER_SEARCH.search(icc_marker_js)
            _injected = False
            if _m:
                _after   = icc_marker_js[_m.end():]
                _brace_i = _after.find('{')
                if _brace_i != -1:
                    # The previous inline counter ignored
                    # string literals, so a "}" INSIDE a default-state string
                    # (e.g. template text like "{choice}") closed the depth
                    # early and the splice corrupted the viewer JS. Reuse the
                    # string/escape-aware extract_balanced_brace_block helper
                    # (identical result when no braces appear inside strings).
                    _block = extract_balanced_brace_block(_after, _brace_i)
                    _state_end = (_brace_i + len(_block)) if _block else -1

                    if _state_end != -1:
                        _abs_start = _m.end() + _brace_i
                        _abs_end   = _m.end() + _state_end
                        _patched_js = (
                            icc_marker_js[:_abs_start]
                            + _app_js
                            + icc_marker_js[_abs_end:]
                        )
                        atomic_write_text(icc_marker_file, _patched_js)
                        logger.info(
                            f"  Marker inject OK: {len(_app_js):,} chars -> "
                            f"{os.path.basename(icc_marker_file)} "
                            f"(was {_abs_end - _abs_start:,} chars default state)"
                        )
                        _injected = True

            if not _injected:
                logger.warning("ICC Plus marker found but injection failed - falling to Strategy C")
                icc_marker_file = None

        # â”€â”€ Strategy C: fetch() patch + prepend data to app.js â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not icc_marker_file:
            logger.info("Offline inject: Strategy C - fetch() patch in app.js")

            atomic_write_text(
                os.path.join(os.path.dirname(index_path), "project.json"),
                project_json_str,
            )

            _fetch_patterns = [
                re.compile(r'fetch\("project\.json"\)'),
                re.compile(r"fetch\('project\.json'\)"),
                re.compile(r'fetch\("\.\/project\.json"\)'),
                # single-quote "./" variant was missing â€”
                # the quote/prefix matrix was asymmetric, so viewers using
                # fetch('./project.json') were silently never patched.
                re.compile(r"fetch\('\.\/project\.json'\)"),
            ]
            _patched_any = False
            for _root, _, _files in os.walk(site_folder):
                for _fname in _files:
                    if not _fname.endswith((".js", ".mjs")):
                        continue
                    _fp = os.path.join(_root, _fname)
                    try:
                        _jt = pathlib.Path(_fp).read_text(encoding="utf-8", errors="replace")
                        if not any(p.search(_jt) for p in _fetch_patterns):
                            continue
                        _preamble = f"window.__CYOA_PROJECT__={data_js};\n"
                        _inline   = (
                            'Promise.resolve(new Response('
                            'JSON.stringify(window.__CYOA_PROJECT__),'
                            '{"headers":{"Content-Type":"application/json"}}'
                            '))'
                        )
                        _pj = _jt
                        for _p in _fetch_patterns:
                            _pj = _p.sub(_inline, _pj)
                        atomic_write_text(_fp, _preamble + _pj)
                        _patched_any = True
                        logger.info(f"  fetch() patched: {os.path.relpath(_fp, site_folder)}")
                    except Exception as _e:
                        logger.warning(f"  Cannot patch {_fname}: {_e}")

            # The docstring has always promised a
            # window.fetch interceptor injected as the first <head> script so
            # the CYOA works on file://, but _build_html_interceptor was never
            # called â€” when no JS file matched the literal fetch patterns
            # (template literals, XHR wrappers, unlisted quote/prefix
            # variants), the offline viewer shipped with only a project.json
            # it could never load from file://. Inject the interceptor ONLY on
            # that previously-broken path (_patched_any False), so output for
            # already-working cases is byte-identical.
            if not _patched_any:
                logger.info("  No fetch() literal matched - injecting <head> fetch interceptor fallback")
                html = _inject_into_head(html, _build_html_interceptor(data_js, size_bytes))

    # Preserve publisher-authored index customizations (title, favicon, extra
    # CSS and scripts) when replacing an ICC Plus 2/Remix online runtime with
    # its local viewer.  Legacy templates already use the source bundle names
    # and keep their established path unchanged.
    if source_html and source_html.strip():
        try:
            from .modernizer import (
                SiteFamily,
                build_preserved_index,
                merge_legacy_index_customizations,
            )

            project_app = (
                parsed_project.get("app")
                if isinstance(parsed_project.get("app"), dict)
                else parsed_project
            )
            project_version = str(
                parsed_project.get("version") or project_app.get("version") or ""
            )
            viewer_type = str(viewer_meta.get("viewer_type", ""))
            runtime_family = str(viewer_meta.get("runtime_family", "") or "")
            preserved_family = None
            if viewer_type == "icc_remix":
                preserved_family = SiteFamily.ICC_REMIX
            elif project_version.startswith("2.") and os.path.isfile(
                os.path.join(site_folder, "js", "app.js")
            ):
                preserved_family = SiteFamily.ICC_PLUS_2
            if preserved_family is not None:
                html = build_preserved_index(
                    source_html,
                    preserved_family,
                    project_json_str,
                )
                logger.info("Preserved source index customizations for %s", preserved_family.value)
            elif runtime_family in {
                "icc_original",
                "icc_plus_legacy",
                "icc_legacy",
                "lt_ouroumov",
            } or viewer_type in {
                "icc_original",
                "icc_plus_legacy",
                "icc_legacy",
                "lt_ouroumov",
            }:
                html = merge_legacy_index_customizations(html, source_html)
                logger.info("Merged publisher customizations into legacy viewer index")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Could not preserve source index customizations: %s", exc)

    # â”€â”€ Apply ICC Plus viewerConfig hints before final write â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    html = _apply_iccplus_viewer_config_to_html(
        html, project_json_str, site_folder, size_bytes, proj_title
    )

    # â”€â”€ Update projectSize div (shared by all strategies) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    html = re.sub(
        r'(<div[^>]+id=["\']projectSize["\'][^>]*>)\s*[^<]*',
        rf'\g<1>{size_bytes}',
        html,
    )

    # â”€â”€ Inject cheat overlay â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Inspired by CYOA Manager's viewer_overlay.js â€” floating gear button
    # that opens a panel to modify points, remove requirements, etc.
    # Works by polling window.app.__vue__.$store.state.app every 500ms.
    # Injected just before </body> so it doesn't interfere with app init.
    _CHEAT_OVERLAY = r"""
<style id="__cyoa_cheat_style__">
#__cyoa_gear{position:fixed;right:10px;bottom:10px;width:32px;height:32px;border:none;border-radius:9px;
display:flex;align-items:center;justify-content:center;
background:rgba(30,36,48,0.75);color:rgba(255,255,255,0.85);
backdrop-filter:blur(8px);box-shadow:0 4px 14px rgba(0,0,0,0.3);
cursor:pointer;z-index:2147483647;font-size:18px;transition:background .15s}
#__cyoa_gear:hover{background:rgba(60,70,90,0.92)}
#__cyoa_panel{position:fixed;right:10px;bottom:50px;width:230px;padding:14px;border-radius:12px;
background:rgba(18,22,32,0.94);color:#e8eeff;
box-shadow:0 12px 32px rgba(0,0,0,0.4);z-index:2147483647;
font:12px/1.5 system-ui,sans-serif;display:none}
#__cyoa_panel.open{display:block}
.__cy_title{font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;
color:rgba(150,170,220,.7);margin-bottom:10px}
.__cy_row{display:flex;gap:8px;margin-bottom:8px;align-items:flex-end}
.__cy_lbl{font-size:10px;color:rgba(200,215,245,.6);margin-bottom:3px}
.__cy_sel,.__cy_inp{background:rgba(255,255,255,.07);border:1px solid rgba(120,140,190,.25);
border-radius:7px;color:#f0f4ff;padding:5px 8px;font:inherit;box-sizing:border-box}
.__cy_sel{appearance:auto;flex:1.2}
.__cy_inp{flex:.8;width:0}
.__cy_btn{width:100%;margin-top:6px;background:rgba(255,255,255,.07);
border:1px solid rgba(120,140,190,.25);border-radius:7px;color:#e8eeff;
padding:7px;font:inherit;cursor:pointer;text-align:left;transition:background .12s}
.__cy_btn:hover{background:rgba(255,255,255,.14)}
.__cy_sep{border:none;border-top:1px solid rgba(120,140,190,.15);margin:8px 0}
/* Autoplay unblock banner */
#__cyoa_audio_banner{position:fixed;top:0;left:0;right:0;padding:10px 16px;
background:rgba(16,20,30,0.92);backdrop-filter:blur(6px);
color:#e8eeff;font:13px/1.4 system-ui,sans-serif;
display:flex;align-items:center;gap:12px;z-index:2147483646;
border-bottom:1px solid rgba(99,140,255,.3)}
#__cyoa_audio_banner button{background:#3b82f6;border:none;border-radius:6px;
color:#fff;padding:6px 14px;font:inherit;cursor:pointer;white-space:nowrap}
</style>
<div id="__cyoa_audio_banner" style="display:none">
  <span>🔇 Audio diblokir browser (autoplay policy)</span>
  <button onclick="__cyoaUnblockAudio()">▶ Aktifkan Audio</button>
  <span style="margin-left:auto;cursor:pointer;opacity:.6" onclick="document.getElementById('__cyoa_audio_banner').style.display='none'">✕</span>
</div>
<div id="__cyoa_panel">
<div class="__cy_title">⚡ Serve Developer Tools</div>
<div class="__cy_row">
<label style="flex:1.2"><div class="__cy_lbl">Point type</div>
<select class="__cy_sel" id="__cyoa_pt_sel"></select></label>
<label style="flex:.8"><div class="__cy_lbl">Value</div>
<input class="__cy_inp" id="__cyoa_pt_val" type="number" step="1"></label>
</div>
<button class="__cy_btn" id="__cyoa_set_pts">💰 Set Points</button>
<hr class="__cy_sep">
<button class="__cy_btn" id="__cyoa_rm_reqs">🔓 Remove All Requirements</button>
<button class="__cy_btn" id="__cyoa_unlim">♾️ Unlimited Choices (all rows)</button>
<button class="__cy_btn" id="__cyoa_sel_all">✅ Select All Choices</button>
<button class="__cy_btn" id="__cyoa_desel_all">☐ Deselect All Choices</button>
</div>
<button id="__cyoa_gear" title="Serve Developer Tools" aria-label="Serve Developer Tools">⚙</button>
<script id="__cyoa_cheat_script__">(function(){
var btn=document.getElementById('__cyoa_gear'),
    panel=document.getElementById('__cyoa_panel'),
    ptSel=document.getElementById('__cyoa_pt_sel'),
    ptVal=document.getElementById('__cyoa_pt_val'),
    audioBanner=document.getElementById('__cyoa_audio_banner');
if(!btn||!panel)return;
btn.onclick=function(){panel.classList.toggle('open');if(panel.classList.contains('open'))refresh()};

// â”€â”€ Autoplay unblock â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// ICC Plus v2 uses No() which calls audio.play() on load.
// If autoplay is blocked, we show a banner so user can explicitly unlock.
var _audioUnblocked=false;
window.__cyoaUnblockAudio=function(){
  _audioUnblocked=true;
  if(audioBanner)audioBanner.style.display='none';
  // Resume any pending audio context
  if(window.AudioContext||window.webkitAudioContext){
    try{var ac=new (window.AudioContext||window.webkitAudioContext)();ac.resume();}catch(e){}
  }
  // Force-play any paused audio elements
  document.querySelectorAll('audio').forEach(function(a){
    if(a.src&&a.paused&&a.readyState>0){a.play().catch(function(){});}
  });
  // Retry ICC Plus bgm
  var app=getApp();
  if(app&&app.bgmId&&window._cyoa_bgm_load){
    try{window._cyoa_bgm_load(app.bgmId);}catch(e){}
  }
};

// Intercept audio play failures and show banner
var _origPlay=HTMLAudioElement.prototype.play;
HTMLAudioElement.prototype.play=function(){
  var self=this, r=_origPlay.call(this);
  if(r&&typeof r.catch==='function'){
    r.catch(function(e){
      var msg=String(e);
      if(!_audioUnblocked&&audioBanner&&
         (msg.indexOf('NotAllowed')!==-1||msg.indexOf('interact')!==-1||msg.indexOf('autoplay')!==-1)){
        audioBanner.style.display='flex';
      }
    });
  }
  return r;
};

function getApp(){try{
// ICC Plus Svelte exposes window.debugApp in official/debug builds. Prefer it
// for inspection; mutation buttons still fall back gracefully if internals differ.
if(window.debugApp){
  if(window.debugApp.app&&Array.isArray(window.debugApp.app.pointTypes))return window.debugApp.app;
  if(window.debugApp.state&&window.debugApp.state.app&&Array.isArray(window.debugApp.state.app.pointTypes))return window.debugApp.state.app;
  if(Array.isArray(window.debugApp.pointTypes))return window.debugApp;
}
var s=window.app&&window.app.__vue__&&window.app.__vue__.$store&&window.app.__vue__.$store.state;
if(s&&s.app&&Array.isArray(s.app.pointTypes))return s.app;
if(window.__pinia){var stores=Object.values(window.__pinia.state.value||{});
for(var i=0;i<stores.length;i++){if(stores[i]&&Array.isArray(stores[i].pointTypes))return stores[i];}}
}catch(e){}return null;}
function refresh(){var app=getApp();if(!app)return;
var pts=app.pointTypes||[];ptSel.innerHTML='';
pts.forEach(function(p,i){if(!p)return;var o=document.createElement('option');
o.value=i;o.textContent=(p.name||('Point '+i))+' ('+Math.round(p.startingSum||0)+')';ptSel.appendChild(o);});
if(ptSel.options.length>0){var idx=parseInt(ptSel.value)||0;
ptVal.value=Math.round((pts[idx]||{}).startingSum||0);}}
ptSel.onchange=function(){var app=getApp();if(!app)return;
var idx=parseInt(ptSel.value)||0;
ptVal.value=Math.round((app.pointTypes[idx]||{}).startingSum||0);};
document.getElementById('__cyoa_set_pts').onclick=function(){var app=getApp();if(!app)return;
var idx=parseInt(ptSel.value)||0,v=parseFloat(ptVal.value);
if(!isNaN(v)&&app.pointTypes[idx]){app.pointTypes[idx].startingSum=v;refresh();}};
document.getElementById('__cyoa_rm_reqs').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){if(!r)return;delete r.requireds;
(r.objects||[]).forEach(function(o){if(o)delete o.requireds;});});
this.textContent='✓ Requirements removed';var self=this;setTimeout(function(){self.textContent='🔓 Remove All Requirements';},1500);};
document.getElementById('__cyoa_unlim').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){if(r)r.allowedChoices=0;});
this.textContent='✓ Done';var self=this;setTimeout(function(){self.textContent='♾️ Unlimited Choices (all rows)';},1500);};
document.getElementById('__cyoa_sel_all').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){(r&&r.objects||[]).forEach(function(o){if(o&&o.id)o.isSelected=true;});});};
document.getElementById('__cyoa_desel_all').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){(r&&r.objects||[]).forEach(function(o){if(o)o.isSelected=false;});});};
var t=setInterval(function(){if(getApp()){if(panel.classList.contains('open'))refresh();clearInterval(t);}},500);
setTimeout(function(){clearInterval(t);},30000);
})();</script>"""

    if "</body>" in html:
        html = html.replace("</body>", _CHEAT_OVERLAY + "\n</body>", 1)
    elif "</html>" in html:
        html = html.replace("</html>", _CHEAT_OVERLAY + "\n</html>", 1)
    else:
        html += _CHEAT_OVERLAY

    # â”€â”€ Copy images/ and audio/ folders directly into the offline viewer â”€â”€â”€
    # The caller passes temp asset folders explicitly. This avoids copying to or
    # deleting output_dir/images and output_dir/audio, which may belong to another run.
    _asset_sources: Dict[str, str] = dict(asset_source_dirs or {})
    if not _asset_sources:
        # Backward-compatible fallback for older callers only. Never delete roots.
        for _asset_dir_name in ("images", "audio"):
            for _candidate in (
                os.path.join(output_dir, file_name, _asset_dir_name),
                os.path.join(output_dir, _asset_dir_name),
            ):
                if os.path.isdir(_candidate):
                    _asset_sources.setdefault(_asset_dir_name, _candidate)
                    break
    for _asset_dir_name in ("images", "audio"):
        _asset_src_dir = _asset_sources.get(_asset_dir_name, "")
        if os.path.isdir(_asset_src_dir):
            _asset_dst = os.path.join(site_folder, _asset_dir_name)
            _n = _copytree_merge_safe(_asset_src_dir, _asset_dst, label=_asset_dir_name)
            if _n:
                logger.info(
                    f"  Copied {_n} {_asset_dir_name} file(s) -> "
                    f"{os.path.relpath(_asset_dst, output_dir)}"
                )

    html = _inject_project_font_links(html, parsed_project)
    html = _localize_preserved_index_assets(
        html,
        source_url,
        site_folder,
    )

    try:
        atomic_write_text(index_path, html)
        logger.info(
            f"OK Offline viewer ready -> {os.path.relpath(index_path, output_dir)} "
            f"({size_bytes/1024/1024:.1f} MB data, viewer: '{viewer_meta.get('name','')}') "
            f"- double-click index.html to play offline."
        )
        return index_path
    except Exception as e:
        logger.error(f"Cannot write patched index.html: {e}")
        shutil.rmtree(site_folder, ignore_errors=True)
        return None


def _v25_manage_offline_viewers(*args, **kwargs):
    from ...gui.final_behaviors import _v25_manage_offline_viewers as _impl
    return _impl(*args, **kwargs)


def _v25_inject_into_viewer(*args, **kwargs):
    from ...gui.final_behaviors import _v25_inject_into_viewer as _impl
    return _impl(*args, **kwargs)


__all__ = [
    "_apply_offline_viewer", "_localize_preserved_index_assets",
    "_inject_project_font_links",
    "_v25_manage_offline_viewers", "_v25_inject_into_viewer",
]

