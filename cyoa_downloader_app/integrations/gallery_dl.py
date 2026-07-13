"""gallery-dl fallback integration.

Phase 28 moves the real implementation out of legacy.py. The small sync
helper keeps legacy module globals readable for external code that still
inspects `_gallery_dl_mode` after changing it through `_set_gallery_dl_mode`.
"""

from __future__ import annotations

import glob as _glob
import os
import subprocess as _sp
import sys
import tempfile
from typing import Dict, Optional, Set
from urllib.parse import urlparse

from ..config.settings import _load_settings, _update_setting
from ..download.asset_scan import _is_probable_raw_cdn_asset
from ..logging_setup import logger
from ..network.proxy import _get_active_proxy


_GALLERY_DL_HOSTS: Dict[str, str] = {
    "www.pixiv.net": "pixiv", "pixiv.net": "pixiv",
    "www.deviantart.com": "deviantart", "deviantart.com": "deviantart",
    "danbooru.donmai.us": "danbooru", "donmai.us": "danbooru",
    "e621.net": "e621", "e926.net": "e621",
    "gelbooru.com": "gelbooru", "hypnohub.net": "hypnohub",
    "rule34.xxx": "rule34", "sankaku.app": "sankaku",
    "chan.sankakucomplex.com": "sankaku",
    "x.com": "twitter", "twitter.com": "twitter", "www.twitter.com": "twitter",
}
_GALLERY_DL_CDN_HOSTS: Set[str] = {
    "i.pximg.net", "img-original.pximg.net", "img-zip-ugoira.pximg.net",
    "pbs.twimg.com", "c.deviantart.com", "a.deviantart.net", "wixmp.com",
    "cdn.donmai.us", "static1.e621.net", "static1.e926.net",
    "img3.sankakucomplex.com", "img.sankakucomplex.com",
    "img1.gelbooru.com", "img2.gelbooru.com", "img.hypnohub.net",
    "img.rule34.xxx", "img3.rule34.xxx", "img.rule34.paheal.net",
}
_gdl_available: Optional[bool] = None   # cached
_gallery_dl_mode: str = str(_load_settings().get("gallery_dl_mode", "off") or "off").lower()
_gallery_dl_path: str = "gallery-dl"
_gallery_dl_config: str = ""


def _sync_legacy_state() -> None:
    mod = sys.modules.get("cyoa_downloader_app.runtime.surface") or sys.modules.get("cyoa_downloader")
    if mod is None:
        return
    for name in ("_gdl_available", "_gallery_dl_mode", "_gallery_dl_path", "_gallery_dl_config"):
        try:
            setattr(mod, name, globals()[name])
        except Exception:
            pass


def _set_gallery_dl_mode(mode: str = "off", *, path: str = "", config: str = "", persist: bool = False) -> None:
    """Set process-local gallery-dl integration mode. Modes: off, smart, force.

    v7.4.0 change: CLI/runtime calls no longer persist the mode implicitly.
    Persist only when the caller explicitly asks to save a user preference.
    """
    global _gallery_dl_mode, _gallery_dl_path, _gallery_dl_config, _gdl_available
    m = (mode or "off").strip().lower()
    if m not in {"off", "smart", "force"}:
        m = "off"
    old_path, old_config = _gallery_dl_path, _gallery_dl_config
    _gallery_dl_mode = m
    _gallery_dl_path = (path or "gallery-dl").strip()
    _gallery_dl_config = (config or "").strip()
    if old_path != _gallery_dl_path or old_config != _gallery_dl_config:
        _gdl_available = None
    if persist:
        try:
            _update_setting("gallery_dl_mode", m)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _set_gallery_dl_mode (line 4178): %s", _ignored_exc)
    _sync_legacy_state()


def _gallery_dl_is_available() -> bool:
    global _gdl_available
    if _gdl_available is not None:
        return _gdl_available
    try:
        import gallery_dl  # noqa
        _gdl_available = True
        _sync_legacy_state()
        return True
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _gallery_dl_is_available (line 4190): %s", _ignored_exc)
    try:
        r = _sp.run([_gallery_dl_path or "gallery-dl", "--version"], capture_output=True, timeout=5)
        _gdl_available = (r.returncode == 0)
        _sync_legacy_state()
        return _gdl_available
    except Exception as exc:
        logger.debug(f"gallery-dl availability probe failed: {exc}")
        _gdl_available = False
        _sync_legacy_state()
        return False


def _is_gallery_dl_candidate(url: str) -> Optional[str]:
    """Return extractor key only when gallery-dl is appropriate for this URL."""
    if _gallery_dl_mode == "off":
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if _gallery_dl_mode == "smart" and _is_probable_raw_cdn_asset(url):
            return None
        if host in _GALLERY_DL_HOSTS:
            return _GALLERY_DL_HOSTS[host]
        if _gallery_dl_mode == "force":
            # Force is an advanced/manual mode. Still avoid obviously local/data URLs.
            if parsed.scheme in {"http", "https"}:
                return host or "custom"
        # Common page patterns. Keep this conservative.
        if any(token in path for token in ("/artworks/", "/posts/", "/post/", "/view/", "/gallery/", "/status/")):
            return host or "page"
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _is_gallery_dl_candidate (line 4231): %s", _ignored_exc)
    return None


# Backwards-compatible name used by older code paths.
def _is_gallery_dl_site(url: str) -> Optional[str]:
    return _is_gallery_dl_candidate(url)


def _fetch_via_gallery_dl(url: str) -> Optional[bytes]:
    """
    Download a single file through gallery-dl only when explicitly enabled.
    Uses gallery/page URLs best. Raw CDN image URLs are skipped in smart mode.
    """
    site = _is_gallery_dl_candidate(url)
    if not site:
        return None
    if not _gallery_dl_is_available():
        logger.debug("[gallery-dl] unavailable; install with: pip install gallery-dl")
        return None

    tmpdir = tempfile.mkdtemp(prefix="cyoa_gdl_")
    try:
        cmd = [
            _gallery_dl_path or "gallery-dl",
            "--destination", tmpdir,
            "--no-mtime",
            "--no-download-archive",
            "--range", "1",
            "-q",
        ]
        if _gallery_dl_config and os.path.exists(_gallery_dl_config):
            cmd.extend(["--config", _gallery_dl_config])
        proxy = _get_active_proxy()
        if proxy:
            cmd.extend(["--proxy", proxy])
        cmd.append(url)

        r = _sp.run(cmd, capture_output=True, timeout=90, text=True)
        image_files = _gdl_collect_files(tmpdir)

        if not image_files:
            logger.debug(f"[gallery-dl] No output for {url} | rc={r.returncode} | stderr={(r.stderr or '')[:200]}")
            return None

        best = max(image_files, key=os.path.getsize)
        with open(best, "rb") as f:
            data = f.read()
        if len(data) < 64:
            return None
        logger.info(f"  [gallery-dl ✓] {os.path.basename(best)} ({len(data)//1024}KB)")
        return data

    except _sp.TimeoutExpired:
        logger.warning(f"[gallery-dl] Timeout: {url}")
        return None
    except Exception as e:
        logger.debug(f"[gallery-dl] {e}")
        return None
    finally:
        import shutil as _sh
        try:
            _sh.rmtree(tmpdir, ignore_errors=True)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _fetch_via_gallery_dl (line 4296): %s", _ignored_exc)


def _gdl_collect_files(directory: str):
    """Collect downloaded media files from gallery-dl output dir."""
    files = _glob.glob(os.path.join(directory, "**", "*"), recursive=True)
    return [
        f for f in files
        if os.path.isfile(f) and not f.lower().endswith((".json", ".log", ".txt", ".part"))
        and os.path.getsize(f) > 64
    ]


__all__ = [
    "_GALLERY_DL_HOSTS", "_GALLERY_DL_CDN_HOSTS", "_gdl_available",
    "_gallery_dl_mode", "_gallery_dl_path", "_gallery_dl_config",
    "_set_gallery_dl_mode", "_gallery_dl_is_available",
    "_is_gallery_dl_candidate", "_is_gallery_dl_site",
    "_fetch_via_gallery_dl", "_gdl_collect_files",
]
