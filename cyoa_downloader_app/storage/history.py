"""Download-history persistence helpers."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from ..logging_setup import logger

_HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".cyoa_downloader", "download_history.json"
)


def _load_history() -> Dict[str, Dict]:
    try:
        if os.path.exists(_HISTORY_FILE):
            with open(_HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    url: entry for url, entry in data.items()
                    if isinstance(url, str) and isinstance(entry, dict)
                }
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _load_history: %s", _ignored_exc)
    return {}


def _save_history(history: Dict[str, Dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        tmp = _HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _HISTORY_FILE)
    except Exception as e:
        logger.debug(f"History save failed: {e}")


def _check_history(url: str) -> Optional[Dict]:
    """Return history entry if URL was previously downloaded, else None."""
    return _load_history().get(url)


_v465_history_lock = threading.RLock()

def _record_history(url: str, file_name: str, mode: str, success: bool) -> None:
    """Persist download history using a streamed one-byte metadata probe."""
    from ..app_info import _APP_DISPLAY_NAME, _APP_VERSION
    from ..core.progress import DownloadCancelledError
    from ..network.fetch import fetch_response
    entry: Dict[str, Any] = {
        "last_downloaded": datetime.now().isoformat(),
        "file_name": file_name,
        "filename": file_name,
        "mode": mode,
        "success": bool(success),
        "url": url,
    }
    if success:
        response: Optional[Any] = None
        try:
            response = fetch_response(
                url,
                timeout=8,
                extra_headers={
                    "User-Agent": f"{_APP_DISPLAY_NAME}/{_APP_VERSION}",
                    "Range": "bytes=0-0",
                    "Accept-Encoding": "identity",
                },
                quiet=True,
                return_error_response=True,
                stream=True,
            )
            if response is not None and int(response.status_code or 0) < 400:
                entry["etag"] = response.headers.get("ETag", "")
                entry["last_modified"] = response.headers.get("Last-Modified", "")
                content_range = response.headers.get("Content-Range", "")
                match = re.search(r"/(\d+)$", content_range)
                entry["content_length"] = (
                    match.group(1) if match else response.headers.get("Content-Length", "")
                )
        except DownloadCancelledError:
            raise
        except Exception as exc:
            logger.debug(f"History metadata probe failed for {url}: {exc}")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception as exc:
                    logger.debug(f"History response close failed for {url}: {exc}")
    with _v465_history_lock:
        history = _load_history()
        history[url] = entry
        if len(history) > 1000:
            oldest = sorted(history, key=lambda item: history[item].get("last_downloaded", ""))
            for old_url in oldest[: len(history) - 1000]:
                history.pop(old_url, None)
        _save_history(history)
