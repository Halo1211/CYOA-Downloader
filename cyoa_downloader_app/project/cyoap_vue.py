"""CYOAP Vue probing and mirror helpers.

Phase 16 moved the low-risk recursive asset scanner and structure probe here
physically. The full site mirror still delegates to legacy until the download
pipeline can be moved without changing output layout/report behavior.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Set
from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - mirrors the other HTML modules
    def BeautifulSoup(*_args, **_kwargs):  # type: ignore
        raise RuntimeError(
            "Missing dependency: beautifulsoup4 is required for CYOAP Vue HTML parsing. "
            "Install with: pip install beautifulsoup4"
        )

from ..app_info import DEFAULT_MAX_WORKERS
from ..constants.assets import (
    AUDIO_EXTENSIONS, FONT_EXTENSIONS, IMAGE_EXTENSIONS, IMAGE_FIELDS,
    SCRIPT_EXTENSIONS, VIDEO_EXTENSIONS,
)
from ..core.url_utils import (
    _candidate_urls_for_cyoap_asset, _cyoap_local_path, _directory_base_url,
    _same_origin,
)
from ..logging_setup import logger
from ..core.atomic_io import atomic_write_bytes, atomic_write_text
from ..core.output import prepare_clean_output_folder
from ..diagnostics.reports import format_backup_report_text, write_asset_failure_summary
from ..download.headers import get_headers_for_url
from ..download.package import zip_temp_folder
from ..download.asset_scan import _safe_response_text
from ..network.fetch import fetch_response


def _legacy():
    """Import legacy lazily for high-risk mirror orchestration only."""
    from ..runtime import surface as _surface
    return _surface


def _response_text(response) -> str:
    """Small local decode helper for probe-only responses."""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    raw = bytes(getattr(response, "content", b"") or b"")
    encoding = getattr(response, "encoding", None) or "utf-8-sig"
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _scan_cyoap_assets(obj, image_set: Set[str], media_set: Set[str]) -> None:
    if obj is None:
        return
    if isinstance(obj, list):
        for item in obj:
            _scan_cyoap_assets(item, image_set, media_set)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                raw = value.strip()
                if not raw or raw.startswith("data:"):
                    continue
                path = urlparse(raw).path.lower()
                ext = os.path.splitext(path)[1]
                if key in IMAGE_FIELDS or ext in IMAGE_EXTENSIONS:
                    image_set.add(raw)
                elif ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
                    media_set.add(raw)
            else:
                _scan_cyoap_assets(value, image_set, media_set)


def _probe_cyoap_vue_structure(base_url: str, timeout: int = 6) -> bool:
    """Validate a CYOAP Vue endpoint pair by content, not HTTP status alone.

    SPA fallback pages frequently return HTTP 200 for arbitrary *.json paths.
    Both endpoints must therefore parse as the expected JSON container types.
    """
    base = _directory_base_url(base_url)
    probes = (
        (urljoin(base, "dist/platform.json"), dict),
        (urljoin(base, "dist/nodes/list.json"), list),
    )
    for endpoint, expected_type in probes:
        response = None
        try:
            response = fetch_response(
                endpoint,
                timeout=max(1, int(timeout)),
                extra_headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"},
                as_bytes=True,
                quiet=True,
                return_error_response=True,
            )
            if response is None or int(getattr(response, "status_code", 0) or 0) != 200:
                return False
            content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
            raw = bytes(getattr(response, "content", b"") or b"")
            if not raw:
                raw = _response_text(response).encode("utf-8", errors="replace")
            stripped = raw.lstrip()
            if "text/html" in content_type or stripped.startswith((b"<!doctype html", b"<html", b"<HTML")):
                logger.debug(f"[Auto-detect] CYOAP probe rejected HTML fallback: {endpoint}")
                return False
            try:
                payload = json.loads(raw.decode(getattr(response, "encoding", None) or "utf-8-sig"))
            except (UnicodeDecodeError, LookupError, json.JSONDecodeError, TypeError, ValueError) as exc:
                # LookupError covers malformed server charset headers naming an
                # unknown codec; retry once with UTF-8 best-effort decode.
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.debug(f"[Auto-detect] CYOAP probe rejected invalid JSON: {endpoint}: {exc}")
                    return False
            if not isinstance(payload, expected_type):
                logger.debug(
                    f"[Auto-detect] CYOAP probe rejected unexpected payload type: "
                    f"{endpoint}: expected {expected_type.__name__}, got {type(payload).__name__}"
                )
                return False
        except Exception as exc:
            logger.debug(f"[Auto-detect] CYOAP structure probe failed: {endpoint}: {exc}")
            return False
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception as exc:
                    logger.debug(f"[Auto-detect] Probe response close failed: {endpoint}: {exc}")
    return True


def try_download_cyoap_vue_site(
    start_url: str,
    output_folder: str,
    *,
    website_zip_output: bool = True,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> bool:
    base_url = _directory_base_url(start_url)
    platform_url = urljoin(base_url, "dist/platform.json")
    list_url = urljoin(base_url, "dist/nodes/list.json")

    # v7.5.5: removed unused `session = create_retry_session()` — this function
    # routes all requests through fetch_response(), the session was never used.
    success_items: List[Dict[str, str]] = []
    failed_items: List[Dict[str, str]] = []
    downloaded_by_kind: Dict[str, List[str]] = {}
    failed_by_kind: Dict[str, List[str]] = {}
    seen_downloads: Set[str] = set()
    seen_lock = threading.Lock()
    image_set: Set[str] = set()
    media_set: Set[str] = set()
    report_lock = threading.Lock()

    def record_success(remote_url: str, local_path: str, kind: str) -> None:
        rel = os.path.relpath(local_path, output_folder).replace("\\", "/")
        with report_lock:
            success_items.append({"url": remote_url, "local": rel, "kind": kind})
            downloaded_by_kind.setdefault(kind, []).append(rel)

    def record_failed(remote_url: str, kind: str, error: str) -> None:
        with report_lock:
            failed_items.append({"url": remote_url, "local": "", "kind": kind, "error": error})
            failed_by_kind.setdefault(kind, []).append(remote_url)

    def fetch_remote(remote_url: str, *, kind: str = "assets", binary: bool = False, referrer: str = ""):
        headers = get_headers_for_url(remote_url) or {"User-Agent": "Mozilla/5.0"}
        if referrer:
            parsed = urlparse(referrer)
            headers.setdefault("Referer", referrer)
            headers.setdefault("Origin", f"{parsed.scheme}://{parsed.netloc}")
        r = None
        try:
            r = fetch_response(remote_url, timeout=30, extra_headers=headers, as_bytes=True)
            if r is None:
                record_failed(remote_url, kind, "request failed")
                return None
            if r.status_code != 200:
                record_failed(remote_url, kind, f"HTTP {r.status_code}")
                return None
            return r.content if binary else _safe_response_text(r)
        except Exception as e:
            record_failed(remote_url, kind, str(e))
            return None
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass

    platform_text = fetch_remote(platform_url, kind="json", binary=False, referrer=base_url)
    if platform_text is None:
        return False
    try:
        platform_obj = json.loads(platform_text)
    except Exception:
        return False

    list_text = fetch_remote(list_url, kind="json", binary=False, referrer=base_url)
    if list_text is None:
        return False

    logger.info("cyoap_vue detected: using dist/platform.json + dist/nodes/list.json flow")
    prepare_clean_output_folder(output_folder)

    platform_local = _cyoap_local_path(output_folder, platform_url)
    os.makedirs(os.path.dirname(platform_local), exist_ok=True)
    pathlib.Path(platform_local).write_text(platform_text, encoding="utf-8")
    seen_downloads.add(platform_url)
    record_success(platform_url, platform_local, "json")
    _scan_cyoap_assets(platform_obj, image_set, media_set)

    try:
        file_list = json.loads(list_text)
        if not isinstance(file_list, list):
            raise ValueError("list.json is not a list")
    except Exception as e:
        raise RuntimeError(f"cyoap_vue list.json invalid: {e}")

    list_local = _cyoap_local_path(output_folder, list_url)
    os.makedirs(os.path.dirname(list_local), exist_ok=True)
    pathlib.Path(list_local).write_text(list_text, encoding="utf-8")
    seen_downloads.add(list_url)
    record_success(list_url, list_local, "json")

    for fname in file_list:
        if not isinstance(fname, str) or not fname.strip():
            continue
        node_url = urljoin(base_url, "dist/nodes/" + fname.strip())
        text = fetch_remote(node_url, kind="json", binary=False, referrer=list_url)
        if text is None:
            continue
        node_local = _cyoap_local_path(output_folder, node_url)
        os.makedirs(os.path.dirname(node_local), exist_ok=True)
        pathlib.Path(node_local).write_text(text, encoding="utf-8")
        seen_downloads.add(node_url)
        record_success(node_url, node_local, "json")
        try:
            _scan_cyoap_assets(json.loads(text), image_set, media_set)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in try_download_cyoap_vue_site (line 1226): %s", _ignored_exc)

    def _download_one_cyoap_asset(args: Tuple[str, str]) -> None:
        """Download a single cyoap_vue asset (image or media), trying candidate URLs in order."""
        item, bucket = args
        if item.startswith("data:"):
            return
        for remote_url in _candidate_urls_for_cyoap_asset(base_url, item, bucket):
            payload = fetch_remote(remote_url, kind=bucket, binary=True, referrer=base_url)
            if payload is None:
                continue
            local_path = _cyoap_local_path(output_folder, remote_url)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            atomic_write_bytes(local_path, payload)
            with seen_lock:
                seen_downloads.add(remote_url)
            record_success(remote_url, local_path, bucket)
            return
        record_failed(item, bucket, "asset not found in candidate cyoap_vue locations")

    all_assets: List[Tuple[str, str]] = (
        [(item, "images") for item in sorted(image_set) if not item.startswith("data:")] +
        [(item, "media")  for item in sorted(media_set)  if not item.startswith("data:")]
    )
    if all_assets:
        logger.info(f"Downloading {len(all_assets)} cyoap_vue asset(s) with {max_workers} thread(s)…")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(_download_one_cyoap_asset, all_assets))

    page_text = fetch_remote(start_url, kind="html", binary=False, referrer=base_url)
    if page_text is not None:
        page_local = _cyoap_local_path(output_folder, start_url)
        os.makedirs(os.path.dirname(page_local), exist_ok=True)
        pathlib.Path(page_local).write_text(page_text, encoding="utf-8")
        seen_downloads.add(start_url)
        record_success(start_url, page_local, "html")

        soup = BeautifulSoup(page_text, "html.parser")
        site_assets: List[str] = []
        for tag in soup.find_all("link"):
            href = tag.get("href", "").strip()
            if href:
                full = urljoin(start_url, href)
                if _same_origin(full, base_url):
                    site_assets.append(full)
        for tag in soup.find_all(["script", "img", "audio", "video", "source"]):
            src = tag.get("src", "").strip()
            if src:
                full = urljoin(start_url, src)
                if _same_origin(full, base_url):
                    site_assets.append(full)
        site_assets.append(urljoin(base_url, "favicon.ico"))

        seen_site: Set[str] = set()
        while site_assets:
            remote_url = site_assets.pop(0)
            if remote_url in seen_site:
                continue
            seen_site.add(remote_url)
            path = urlparse(remote_url).path.lower()
            ext = os.path.splitext(path)[1]
            if ext in FONT_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | {".ico"}:
                payload = fetch_remote(remote_url, kind="assets", binary=True, referrer=start_url)
                if payload is None:
                    continue
                local_path = _cyoap_local_path(output_folder, remote_url)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                atomic_write_bytes(local_path, payload)
                seen_downloads.add(remote_url)
                record_success(remote_url, local_path, "assets")
                continue

            text_payload = fetch_remote(remote_url, kind="assets", binary=False, referrer=start_url)
            if text_payload is None:
                continue
            local_path = _cyoap_local_path(output_folder, remote_url)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            atomic_write_text(local_path, text_payload)
            seen_downloads.add(remote_url)
            kind = "css" if ext == ".css" else "js" if ext in SCRIPT_EXTENSIONS else "assets"
            record_success(remote_url, local_path, kind)

            if ext == ".css":
                for m in re.finditer(r'@import\s+(?:url\()?["\']?([^"\')\s]+)', text_payload, re.IGNORECASE):
                    child = urljoin(remote_url, m.group(1))
                    if _same_origin(child, base_url):
                        site_assets.append(child)
                for m in re.finditer(r'url\(([^)]+)\)', text_payload, re.IGNORECASE):
                    raw = m.group(1).strip().strip("'\"")
                    if not raw or raw.startswith("data:"):
                        continue
                    child = urljoin(remote_url, raw)
                    if _same_origin(child, base_url):
                        site_assets.append(child)

    report_path = os.path.join(output_folder, "backup_report.txt")
    report_text = format_backup_report_text(
        start_url=start_url,
        project_url=platform_url,
        project_root="dist/platform.json",
        downloaded=success_items,
        failed=failed_items,
        downloaded_groups=downloaded_by_kind,
        failed_groups=failed_by_kind,
        notes=["Engine mode: cyoap_vue", "Downloaded dist/platform.json and dist/nodes/list.json when available."],
    )
    pathlib.Path(report_path).write_text(report_text, encoding="utf-8")
    logger.info("cyoap_vue backup report saved: backup_report.txt")
    if failed_items:
        try:
            write_asset_failure_summary(
                failed_items, output_folder, source_url=start_url,
                title="Broken cyoap_vue Asset Report"
            )
        except Exception as e:
            logger.debug(f"Broken asset report could not be written: {e}")

    if website_zip_output:
        zip_name = os.path.basename(output_folder.rstrip(os.sep)) + ".zip"
        logger.info(f"Zipping → {zip_name}")
        zip_temp_folder(output_folder, zip_name=zip_name)
        shutil.rmtree(output_folder, ignore_errors=True)
    else:
        logger.info(f"ICC folder kept: {output_folder}")

    logger.info("cyoap_vue website download complete.")
    return True


__all__ = [
    "_scan_cyoap_assets",
    "try_download_cyoap_vue_site",
    "_probe_cyoap_vue_structure",
]
