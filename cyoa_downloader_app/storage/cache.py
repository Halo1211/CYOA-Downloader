"""Persistent disk image cache and coalesced index persistence."""

from __future__ import annotations

import atexit
import hashlib as _hashlib
import json as _json_cache
import pathlib
import threading
import time as _time
from typing import Dict, Optional

from ..core.atomic_io import atomic_write_bytes, atomic_write_text
from ..logging_setup import logger

_CACHE_DIR = pathlib.Path.home() / ".cyoa_downloader" / "image_cache"
_CACHE_IDX = _CACHE_DIR / "index.json"
_cache_index: Dict[str, str] = {}
_cache_lock = threading.Lock()
_cache_loaded = False


def _cache_load() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    with _cache_lock:
        if _cache_loaded:
            return
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            if _CACHE_IDX.exists():
                with open(_CACHE_IDX, encoding="utf-8") as f:
                    data = _json_cache.load(f)
                if isinstance(data, dict):
                    _cache_index.update({
                        url: digest.lower()
                        for url, digest in data.items()
                        if isinstance(url, str)
                        and isinstance(digest, str)
                        and len(digest) == 64
                        and all(ch in "0123456789abcdefABCDEF" for ch in digest)
                    })
            _cache_loaded = True
            logger.debug(f"Image cache: {len(_cache_index)} entries loaded")
        except Exception as e:
            logger.debug(f"Image cache load failed: {e}")


def _cache_get(url: str) -> Optional[bytes]:
    """Return cached bytes for URL if available and valid."""
    _cache_load()
    with _cache_lock:
        h = _cache_index.get(url)
    if not h:
        return None
    fpath = _CACHE_DIR / h[:2] / h
    if fpath.exists():
        try:
            data = fpath.read_bytes()
            if _hashlib.sha256(data).hexdigest() == h:
                return data
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _cache_get: %s", _ignored_exc)
    with _cache_lock:
        _cache_index.pop(url, None)
    return None


def _cache_stats() -> Dict[str, int]:
    _cache_load()
    return {
        "entries": len(_cache_index),
        "size_mb": sum(
            ((_CACHE_DIR / h[:2] / h).stat().st_size
             for h in _cache_index.values()
             if (_CACHE_DIR / h[:2] / h).exists()),
            0,
        ) // (1024 * 1024),
    }


def _clear_image_cache() -> int:
    """Remove all cached images. Returns number of files deleted."""
    global _cache_index
    count = 0
    try:
        if _CACHE_DIR.exists():
            for item in _CACHE_DIR.iterdir():
                if item.is_dir():
                    for f in item.iterdir():
                        f.unlink(missing_ok=True)
                        count += 1
                    try:
                        item.rmdir()
                    except OSError as _ignored_exc:
                        logger.debug("Ignored recoverable exception in _clear_image_cache: %s", _ignored_exc)
            if _CACHE_IDX.exists():
                _CACHE_IDX.unlink()
        _cache_index = {}
        logger.info(f"Image cache cleared — {count} file(s) removed")
    except Exception as e:
        logger.warning(f"Cache clear error: {e}")
    return count


_v465_cache_save_event = threading.Event()
_v465_cache_writer_lock = threading.Lock()
_v465_cache_writer_thread: Optional[threading.Thread] = None


def _v465_flush_cache_index() -> None:
    try:
        if not _cache_loaded and not _cache_index:
            return
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _cache_lock:
            snapshot = dict(_cache_index)
        atomic_write_text(
            str(_CACHE_IDX),
            _json_cache.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        )
    except Exception as exc:
        logger.debug(f"Image cache index flush failed: {exc}")


def _v465_cache_writer() -> None:
    while True:
        _v465_cache_save_event.wait()
        _v465_cache_save_event.clear()
        _time.sleep(0.20)
        while _v465_cache_save_event.is_set():
            _v465_cache_save_event.clear()
            _time.sleep(0.10)
        _v465_flush_cache_index()


def _v465_schedule_cache_save() -> None:
    global _v465_cache_writer_thread
    with _v465_cache_writer_lock:
        if _v465_cache_writer_thread is None or not _v465_cache_writer_thread.is_alive():
            _v465_cache_writer_thread = threading.Thread(
                target=_v465_cache_writer,
                name="cyoa-cache-index-writer",
                daemon=True,
            )
            _v465_cache_writer_thread.start()
    _v465_cache_save_event.set()


def _cache_put(url: str, data: bytes) -> None:
    """Store image bytes and coalesce cache-index persistence into one thread."""
    if not data or len(data) < 64:
        return
    try:
        _cache_load()
        digest = _hashlib.sha256(data).hexdigest()
        folder = _CACHE_DIR / digest[:2]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / digest
        if not target.exists():
            atomic_write_bytes(str(target), data)
        with _cache_lock:
            _cache_index[url] = digest
        _v465_schedule_cache_save()
    except Exception as exc:
        logger.debug(f"Image cache put failed: {exc}")


atexit.register(_v465_flush_cache_index)
