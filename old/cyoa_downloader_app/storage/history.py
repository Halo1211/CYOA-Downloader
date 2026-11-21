"""Download-history persistence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any

from ..core.atomic_io import atomic_write_text, identity_response_total_length, interprocess_file_lock
from ..logging_setup import logger
from .history_metadata import HistoryMetadataQueue

_HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".cyoa_downloader", "download_history.json"
)


def _load_history() -> dict[str, dict]:
    return _read_history_path(_HISTORY_FILE)


def _read_history_path(path) -> dict[str, dict]:
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    url: entry for url, entry in data.items()
                    if isinstance(url, str) and isinstance(entry, dict)
                }
    except (OSError, UnicodeError, TypeError, ValueError, RecursionError) as _ignored_exc:
        logger.debug("Ignored recoverable exception in _load_history: %s", _ignored_exc)
    return {}


def _save_history(history: dict[str, dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        atomic_write_text(
            _HISTORY_FILE,
            json.dumps(history, indent=2, ensure_ascii=False),
        )
    except (OSError, UnicodeError, TypeError, ValueError, RecursionError) as e:
        logger.debug(f"History save failed: {e}")


def _check_history(url: str) -> dict | None:
    """Return history entry if URL was previously downloaded, else None."""
    return _load_history().get(url)


_v465_history_lock = threading.RLock()
_metadata_queue = HistoryMetadataQueue()
_BACKGROUND_PROBE_ERRORS = (Exception,)


def _probe_history_metadata(url):
    from ..app_info import _APP_DISPLAY_NAME, _APP_VERSION
    from ..core.progress import DownloadCancelledError
    from ..network.fetch import fetch_response
    response = None
    try:
        response = fetch_response(url, timeout=8, extra_headers={
            "User-Agent": f"{_APP_DISPLAY_NAME}/{_APP_VERSION}", "Range": "bytes=0-0", "Accept-Encoding": "identity",
        }, quiet=True, return_error_response=True, stream=True)
        if response is not None and 0 < int(response.status_code or 0) < 400:
            length = identity_response_total_length(response)
            values = {"etag": response.headers.get("ETag", ""),
                      "last_modified": response.headers.get("Last-Modified", ""),
                      "content_length": str(length) if length is not None else ""}
            return values, "ready" if any(values.values()) else "unavailable"
    except DownloadCancelledError:
        logger.debug("History metadata probe cancelled after completed download: %s", url)
        return {}, "cancelled"
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("History metadata probe failed for %s: %s", url, exc)
    finally:
        if response is not None:
            try:
                response.close()
            except (AttributeError, OSError, RuntimeError, ValueError) as exc:
                logger.debug("History response close failed for %s: %s", url, exc)
    return {}, "unavailable"


def _write_history_path(path, history):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_text(path, json.dumps(history, indent=2, ensure_ascii=False))


def _merge_metadata(path, url, token, values, state):
    with _v465_history_lock, interprocess_file_lock(path):
        history = _read_history_path(path)
        entry = history.get(url)
        if entry is None or entry.get("_metadata_id") != token or not entry.get("success"):
            return
        entry.update(values)
        entry["metadata_state"] = state
        _write_history_path(path, history)


def _metadata_context(url):
    from urllib.parse import urlparse

    from ..network.proxy import _get_active_proxies
    from ..network.sessions import _get_shared_session
    from ..runtime import state

    # Reuse the live session without waiting on unrelated session initialization.
    session = state._shared_session or _get_shared_session(False)
    host = (urlparse(url).hostname or "").lower()
    cookies = [(c.name, c.value, c.domain, c.path, c.secure, c.expires) for c in session.cookies
               if not c.domain or host == c.domain.lstrip(".").lower() or host.endswith("." + c.domain.lstrip(".").lower())]
    profile = tuple(getattr(state, name, None) for name in
                    ("_proxy_mode", "_proxy_http", "_proxy_https", "_proxy_no_proxy", "_dns_protocol", "_dns_port",
                     "_dns_timeout", "_dns_fallback_system", "_dns_ipv6"))
    environment = tuple((name, os.environ.get(name)) for name in
                        ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"))
    values = (sorted(session.headers.items()), sorted(cookies, key=repr), _get_active_proxies(), profile, environment)
    return session, hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _cancel_history_metadata(owner):
    _metadata_queue.cancel_owner(owner)


def _same_metadata_context(url, expected):
    current = _metadata_context(url)
    return current[0] is expected[0] and current[1] == expected[1]


def _defer_metadata(path, url, token, owner):
    from ..network.budget import request_scope

    context = _metadata_context(url)
    def work(cancel):
        values, state = {}, "unavailable"
        try:
            if owner is not None and owner.is_set():
                cancel.set()
            if cancel.is_set():
                state = "cancelled"
            elif _same_metadata_context(url, context):
                with request_scope(priority=20, cancel_event=cancel):
                    values, state = _probe_history_metadata(url)
                if owner is not None and owner.is_set():
                    cancel.set()
                if cancel.is_set():
                    values, state = {}, "cancelled"
                elif not _same_metadata_context(url, context):
                    values, state = {}, "unavailable"
        except _BACKGROUND_PROBE_ERRORS:
            # Optional transport/context callbacks cannot strand a pending
            # history entry or relabel an already completed download.
            logger.exception("Background history metadata unavailable")
            values, state = {}, "unavailable"
        _merge_metadata(path, url, token, values, state)
    if not _metadata_queue.submit((path, url), work, owner=owner):
        _merge_metadata(path, url, token, {}, "skipped")


def _record_history(url: str, file_name: str, mode: str, success: bool, *, background=False, cancel_event=None,
                    response_metadata=None) -> None:
    """Persist success immediately when GUI metadata is deferred; retain the sync API."""
    token = uuid.uuid4().hex
    path = os.path.abspath(_HISTORY_FILE)
    entry: dict[str, Any] = {
        "last_downloaded": datetime.now().astimezone().isoformat(),
        "file_name": file_name,
        "filename": file_name,
        "mode": mode,
        "success": bool(success),
        "url": url,
        "_metadata_id": token,
    }
    reused = None
    if success and response_metadata is not None:
        try:
            reused = response_metadata.take(url)
        except _BACKGROUND_PROBE_ERRORS as exc:
            logger.debug("Could not reuse response metadata: %s", exc)
    if reused is not None:
        entry.update(reused)
        entry["metadata_state"] = "ready"
        entry["metadata_source"] = "download_response"
    elif success and not background:
        values, state = _probe_history_metadata(url)
        entry.update(values)
        entry["metadata_state"] = state
    elif success:
        entry["metadata_state"] = "pending"
    try:
        with _v465_history_lock, interprocess_file_lock(path):
            history = _read_history_path(path)
            history[url] = entry
            if len(history) > 1000:
                def timestamp(item: str) -> str:
                    value = history[item].get("last_downloaded", "")
                    return value if isinstance(value, str) else ""

                oldest = sorted(history, key=timestamp)
                for old_url in oldest[: len(history) - 1000]:
                    history.pop(old_url, None)
            _write_history_path(path, history)
        if success and background and reused is None:
            try:
                _defer_metadata(path, url, token, cancel_event)
            except _BACKGROUND_PROBE_ERRORS as exc:
                logger.debug("Could not schedule history metadata: %s", exc)
                _merge_metadata(path, url, token, {}, "unavailable")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        # History is auxiliary state. Lock contention, permissions, or a
        # damaged history directory must never turn an otherwise successful
        # download into a failed batch job (nor abort processing later rows).
        logger.warning("Could not record download history for %s: %s", url, exc)


_record_history.supports_background_metadata = True
_record_history.supports_response_metadata = True
