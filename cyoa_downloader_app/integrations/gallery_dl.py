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
import time
from urllib.parse import urlparse

from ..config.settings import _load_settings, _update_setting
from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..download.asset_scan import _is_probable_raw_cdn_asset
from ..logging_setup import logger
from ..network.proxy import _get_active_proxy, _should_bypass_manual_proxy

_GALLERY_DL_HOSTS: dict[str, str] = {
    "www.pixiv.net": "pixiv", "pixiv.net": "pixiv",
    "www.deviantart.com": "deviantart", "deviantart.com": "deviantart",
    "danbooru.donmai.us": "danbooru", "donmai.us": "danbooru",
    "e621.net": "e621", "e926.net": "e621",
    "gelbooru.com": "gelbooru", "hypnohub.net": "hypnohub",
    "rule34.xxx": "rule34", "sankaku.app": "sankaku",
    "zerochan.net": "zerochan", "www.zerochan.net": "zerochan",
    "chan.sankakucomplex.com": "sankaku",
    "x.com": "twitter", "twitter.com": "twitter", "www.twitter.com": "twitter",
}
_GALLERY_DL_CDN_HOSTS: set[str] = {
    "i.pximg.net", "img-original.pximg.net", "img-zip-ugoira.pximg.net",
    "pbs.twimg.com", "c.deviantart.com", "a.deviantart.net", "wixmp.com",
    "cdn.donmai.us", "static1.e621.net", "static1.e926.net",
    "img3.sankakucomplex.com", "img.sankakucomplex.com",
    "img1.gelbooru.com", "img2.gelbooru.com", "img.hypnohub.net",
    "img.rule34.xxx", "img3.rule34.xxx", "img.rule34.paheal.net",
}
_gdl_available: bool | None = None   # cached
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
        except (AttributeError, KeyError, TypeError) as exc:
            logger.debug("Could not mirror gallery-dl state %s: %s", name, exc)


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
        except (OSError, TypeError, ValueError) as _ignored_exc:
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
        r = _sp.run([_gallery_dl_path or "gallery-dl", "--version"], capture_output=True, timeout=5, check=False)
        _gdl_available = (r.returncode == 0)
        _sync_legacy_state()
        return _gdl_available
    except (OSError, _sp.SubprocessError) as exc:
        logger.debug(f"gallery-dl availability probe failed: {exc}")
        _gdl_available = False
        _sync_legacy_state()
        return False


def _is_gallery_dl_candidate(url: str) -> str | None:
    """Return extractor key only when gallery-dl is appropriate for this URL."""
    if _gallery_dl_mode == "off":
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() not in {"http", "https"} or not host:
            return None
        path = parsed.path.lower()
        if _gallery_dl_mode == "smart" and _is_probable_raw_cdn_asset(url):
            return None
        if host in _GALLERY_DL_HOSTS:
            return _GALLERY_DL_HOSTS[host]
        if _gallery_dl_mode == "force" and parsed.scheme in {"http", "https"}:
            # Force is an advanced/manual mode. Still avoid obviously local/data URLs.
            return host or "custom"
        # Common page patterns. Keep this conservative.
        if any(token in path for token in ("/artworks/", "/posts/", "/post/", "/view/", "/gallery/", "/status/")):
            return host or "page"
    except (AttributeError, TypeError, ValueError) as _ignored_exc:
        logger.debug("Ignored recoverable exception in _is_gallery_dl_candidate (line 4231): %s", _ignored_exc)
    return None


# Backwards-compatible name used by older code paths.
def _is_gallery_dl_site(url: str) -> str | None:
    return _is_gallery_dl_candidate(url)


def _run_gallery_dl(cmd: list[str]) -> tuple[int, str]:
    """Run a bounded gallery job with cancellation and bounded diagnostic output."""
    _raise_if_cancelled()
    with tempfile.TemporaryFile() as output:
        proc = _sp.Popen(
            cmd, stdout=output, stderr=_sp.STDOUT,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 90
        try:
            while True:
                _raise_if_cancelled()
                try:
                    returncode = proc.wait(timeout=0.25)
                    break
                except _sp.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise _sp.TimeoutExpired(cmd, 90)
        except (DownloadCancelledError, _sp.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except _sp.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        _raise_if_cancelled()
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 4096))
        return returncode, output.read().decode("utf-8", "replace")


def _fetch_via_gallery_dl(url: str) -> bytes | None:
    """
    Download a single file through gallery-dl only when explicitly enabled.
    Uses gallery/page URLs best. Raw CDN image URLs are skipped in smart mode.
    """
    _raise_if_cancelled()
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
        if proxy and not _should_bypass_manual_proxy(url):
            cmd.extend(["--proxy", proxy])
        cmd.append(url)

        _raise_if_cancelled()
        returncode, diagnostics = _run_gallery_dl(cmd)
        image_files = _gdl_collect_files(tmpdir)

        if not image_files:
            logger.debug(f"[gallery-dl] No output for {url} | rc={returncode} | output={diagnostics[:200]}")
            return None

        best = max(image_files, key=os.path.getsize)
        with open(best, "rb") as f:
            data = f.read()
        if len(data) < 64:
            return None
        logger.info(f"  [gallery-dl ✓] {os.path.basename(best)} ({len(data)//1024}KB)")
        return data

    except DownloadCancelledError:
        raise
    except _sp.TimeoutExpired:
        logger.warning(f"[gallery-dl] Timeout: {url}")
        return None
    except (OSError, RuntimeError, TypeError, ValueError, _sp.SubprocessError) as e:
        logger.debug(f"[gallery-dl] {e}")
        return None
    finally:
        import shutil as _sh
        try:
            _sh.rmtree(tmpdir, ignore_errors=True)
        except OSError as _ignored_exc:
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
    "_GALLERY_DL_CDN_HOSTS",
    "_GALLERY_DL_HOSTS",
    "_fetch_via_gallery_dl",
    "_gallery_dl_config",
    "_gallery_dl_is_available",
    "_gallery_dl_mode",
    "_gallery_dl_path",
    "_gdl_available",
    "_gdl_collect_files",
    "_is_gallery_dl_candidate",
    "_is_gallery_dl_site",
    "_set_gallery_dl_mode",
]
