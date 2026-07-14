"""Direct offline gallery exporter for static cyoa.cafe catalogue records."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
import json
import os
from typing import Any, Dict, Iterable, List, Tuple

from .package import atomic_stream_response_to_file, clean_url_path_component
from ..constants.assets import IMAGE_EXTENSIONS
from ..core.atomic_io import atomic_write_text
from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from ..network.fetch import fetch_response
from ..project.cyoa_cafe import build_cyoa_cafe_file_url, classify_cyoa_cafe_record


def _record_files(record: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Return unique (kind, remote filename, relative local path) entries."""
    entries: List[Tuple[str, str, str]] = []
    seen = set()

    def add(kind: str, names: Iterable[Any], folder: str) -> None:
        for index, raw in enumerate(names, 1):
            name = str(raw or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            safe = clean_url_path_component(os.path.basename(name.replace("\\", "/")))
            stem, ext = os.path.splitext(safe)
            if ext.lower() not in IMAGE_EXTENSIONS:
                # The API is intended for image pages; unknown file types are
                # not mirrored into an executable offline package.
                logger.warning("Skipping non-image cyoa.cafe page file: %s", name)
                continue
            local = os.path.join("images", folder, f"{index:04d}_{stem}{ext.lower()}")
            entries.append((kind, name, local))

    add("page", record.get("cyoa_pages") if isinstance(record.get("cyoa_pages"), list) else [], "pages")
    add(
        "preview",
        record.get("cyoa_pages_preview") if isinstance(record.get("cyoa_pages_preview"), list) else [],
        "previews",
    )
    cover = str(record.get("image") or "").strip()
    if cover:
        add("cover", [cover], "cover")
    return entries


def _download_one(record: Dict[str, Any], folder: str, entry: Tuple[str, str, str]) -> Dict[str, Any]:
    _raise_if_cancelled()
    kind, remote_name, relative = entry
    url = build_cyoa_cafe_file_url(record, remote_name)
    target = os.path.abspath(os.path.join(folder, relative))
    root = os.path.abspath(folder)
    if os.path.commonpath([root, target]) != root:
        raise ValueError("unsafe cyoa.cafe local path")
    response = fetch_response(
        url,
        timeout=60,
        stream=True,
        return_error_response=True,
        extra_headers={"Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.1"},
    )
    if response is None or int(getattr(response, "status_code", 0) or 0) >= 400:
        status = int(getattr(response, "status_code", 0) or 0) if response is not None else 0
        if response is not None:
            response.close()
        raise IOError(f"HTTP {status or 'request failed'}")
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    if content_type and not content_type.startswith("image/"):
        response.close()
        raise IOError(f"unexpected content type {content_type}")
    try:
        size = atomic_stream_response_to_file(response, target)
    finally:
        response.close()
    return {
        "kind": kind,
        "source_name": remote_name,
        "url": url,
        "local": relative.replace("\\", "/"),
        "bytes": size,
        "content_type": content_type,
    }


def _gallery_html(record: Dict[str, Any], pages: List[Dict[str, Any]], cover: str) -> str:
    title = escape(str(record.get("title") or "CYOA.CAFE Static Archive"))
    source = escape(f"https://cyoa.cafe/game/{record.get('id', '')}", quote=True)
    figures = "\n".join(
        f'<figure><img src="{escape(item["local"], quote=True)}" loading="lazy" '
        f'alt="Page {index}"><figcaption>Page {index}</figcaption></figure>'
        for index, item in enumerate(pages, 1)
    )
    cover_html = (
        f'<img class="cover" src="{escape(cover, quote=True)}" alt="Cover">' if cover else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark}}body{{margin:0;background:#0b0f16;color:#e5e7eb;font:15px system-ui,sans-serif}}
header{{position:sticky;top:0;z-index:2;padding:12px 18px;background:#111827ee;border-bottom:1px solid #263244}}
h1{{font-size:18px;margin:0 0 4px}}a{{color:#67e8f9}}main{{max-width:1500px;margin:auto;padding:16px}}
.cover{{display:block;max-width:min(460px,100%);max-height:420px;object-fit:contain;margin:0 auto 18px;border-radius:10px}}
figure{{margin:0 0 18px;background:#111827;border:1px solid #263244;border-radius:10px;overflow:hidden}}
figure img{{display:block;width:100%;height:auto}}figcaption{{padding:8px 12px;color:#94a3b8}}
</style></head><body><header><h1>{title}</h1><a href="{source}">Original CYOA.CAFE record</a></header>
<main>{cover_html}{figures}</main></body></html>"""


def download_cyoa_cafe_static_record(
    record: Dict[str, Any],
    folder: str,
    *,
    source_url: str,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """Download a static-page record and build a backend-free offline viewer."""
    if classify_cyoa_cafe_record(record) != "static_pages":
        raise ValueError("cyoa.cafe record does not contain static pages")
    os.makedirs(folder, exist_ok=True)
    entries = _record_files(record)
    _raise_if_cancelled()
    if not any(kind == "page" for kind, _name, _local in entries):
        raise ValueError("cyoa.cafe record contains no supported image pages")
    downloaded: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    workers = max(1, min(16, int(max_workers or 4)))
    executor = ThreadPoolExecutor(max_workers=workers)
    future_map = {executor.submit(_download_one, record, folder, entry): entry for entry in entries}
    try:
        for future in as_completed(future_map):
            _raise_if_cancelled()
            kind, remote_name, _local = future_map[future]
            try:
                downloaded.append(future.result())
            except DownloadCancelledError:
                raise
            except Exception as exc:
                failures.append({"kind": kind, "source_name": remote_name, "error": str(exc)})
                logger.warning("cyoa.cafe static file failed (%s): %s", remote_name, exc)
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=False, cancel_futures=True)
    _raise_if_cancelled()
    downloaded.sort(key=lambda item: (item["kind"] != "page", item["local"]))
    pages = [item for item in downloaded if item["kind"] == "page"]
    if not pages:
        raise RuntimeError("No cyoa.cafe static pages could be downloaded")
    cover = next((item["local"] for item in downloaded if item["kind"] == "cover"), "")
    atomic_write_text(os.path.join(folder, "index.html"), _gallery_html(record, pages, cover))
    safe_metadata = {
        key: record.get(key)
        for key in (
            "id", "collectionId", "collectionName", "title", "description", "language",
            "created", "updated", "bumped_at", "img_or_link", "iframe_url",
            "original_link", "cyoa_pages", "cyoa_pages_preview", "image",
        )
        if key in record
    }
    atomic_write_text(
        os.path.join(folder, "cyoa_cafe_metadata.json"),
        json.dumps(safe_metadata, indent=2, ensure_ascii=False),
    )
    manifest = {
        "format": 1,
        "detected_engine": "cyoa_cafe_static",
        "selected_pipeline": ["metadata_api", "direct_files", "offline_gallery"],
        "source_url": source_url,
        "record_id": record.get("id"),
        "downloaded": downloaded,
        "failures": failures,
        "browser_skipped_reason": "all page assets resolved from structured metadata",
        "safe_interactions": 0,
    }
    atomic_write_text(
        os.path.join(folder, "archive_manifest.json"),
        json.dumps(manifest, indent=2, ensure_ascii=False),
    )
    logger.info(
        "CYOA.CAFE static archive: %d page(s), %d auxiliary file(s), %d failed",
        len(pages), len(downloaded) - len(pages), len(failures),
    )
    return manifest


__all__ = ["download_cyoa_cafe_static_record"]
