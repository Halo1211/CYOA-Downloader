"""Desktop notification and update-check helpers moved out of legacy.py."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from ..app_info import _APP_VERSION, _GITHUB_RELEASE_API
from ..logging_setup import logger
from ..network.fetch import fetch_response


def _send_desktop_notification(title: str, message: str) -> None:
    """Send a desktop notification via plyer (best-effort, non-blocking)."""
    def _do():
        try:
            from plyer import notification  # type: ignore
            notification.notify(
                title=title, message=message[:256],
                timeout=5, app_name="CYOA Downloader",
            )
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _do: %s", _ignored_exc)
    threading.Thread(target=_do, daemon=True).start()


def _check_for_app_updates() -> Optional[Dict[str, str]]:
    """Check GitHub Releases API for a newer version.

    Returns {"version": ..., "url": ..., "notes": ...} if newer, else None.
    """
    if not _GITHUB_RELEASE_API:
        return None
    r = None
    try:
        r = fetch_response(_GITHUB_RELEASE_API, timeout=8,
                           extra_headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "CYOA-Downloader"},
                           as_bytes=True)
        if r is None or r.status_code != 200:
            return None
        data = r.json()
        remote_tag = data.get("tag_name", "").lstrip("vV").strip()
        if not remote_tag:
            return None
        def _ver(s):
            # Old parser dropped any component that wasn't
            # pure digits, so "1.0.2-rev4" → (1, 0) and the identical remote
            # "1.0.2" → (1, 0, 2) looked "newer" — a false update prompt on
            # every rev-suffixed build. Take the leading digits of each
            # component and pad to 3 so tuple lengths always match.
            parts = []
            for x in s.split(".")[:3]:
                m = re.match(r"\d+", x.strip())
                parts.append(int(m.group(0)) if m else 0)
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        if _ver(remote_tag) > _ver(_APP_VERSION):
            return {
                "version": remote_tag,
                "url": data.get("html_url", ""),
                "notes": (data.get("body") or "")[:500],
            }
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _check_for_app_updates: %s", _ignored_exc)
    finally:
        if r is not None:
            try:
                r.close()
            except Exception as _close_exc:
                logger.debug("Update response close failed: %s", _close_exc)
    return None


def _batch_check_updates(history: Dict[str, Dict],
                         max_workers: int = 4,
                         progress_cb=None) -> List[Dict]:
    """Check previously downloaded CYOAs for server-side changes.

    Compares stored Content-Length / Last-Modified / ETag against current
    server HEAD response.
    """
    results: List[Dict] = []
    entries = [
        (url, meta) for url, meta in history.items()
        if isinstance(url, str) and isinstance(meta, dict) and meta.get("success")
    ]
    total = len(entries)
    if not total:
        return results

    def _check(args):
        idx, (url, meta) = args
        r = None
        try:
            # The recorder (_record_history probe) stores
            # the IDENTITY length taken from a "Range: bytes=0-0" +
            # "Accept-Encoding: identity" probe (Content-Range total). This
            # checker did a plain full GET where requests advertises gzip, so
            # servers returned the COMPRESSED Content-Length — every gzipped,
            # unchanged file was falsely reported "updated (size X→Y)".
            # Reproduced with a local HTTP fixture. Fix: probe with the exact
            # same semantics as the recorder (also stops downloading the full
            # body of every history URL just to read headers).
            r = fetch_response(
                url,
                timeout=10,
                extra_headers={
                    "User-Agent": "Mozilla/5.0",
                    "Range": "bytes=0-0",
                    "Accept-Encoding": "identity",
                },
                quiet=True,
                return_error_response=True,
                stream=True,
            )
            _status = int(getattr(r, "status_code", 0) or 0)
            if r is None or _status == 0 or _status >= 400:
                return {"url": url, "name": meta.get("filename", ""),
                        "status": "unreachable", "reason": f"HTTP {_status or 'request failed'}"}
            old_etag = meta.get("etag", "")
            old_lm   = meta.get("last_modified", "")
            old_cl   = meta.get("content_length", "")
            new_etag = r.headers.get("ETag", "")
            new_lm   = r.headers.get("Last-Modified", "")
            # Identity total from Content-Range, same
            # extraction as the recorder; Content-Length is the fallback for
            # servers that ignore Range (then it is the identity full length,
            # because we requested Accept-Encoding: identity).
            _cr_match = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
            new_cl = _cr_match.group(1) if _cr_match else r.headers.get("Content-Length", "")
            changed, reason = False, []
            if old_etag and new_etag and old_etag != new_etag:
                changed = True; reason.append("ETag")
            if old_lm and new_lm and old_lm != new_lm:
                changed = True; reason.append("Last-Modified")
            if old_cl and new_cl and old_cl != new_cl:
                changed = True; reason.append(f"size {old_cl}→{new_cl}")
            return {"url": url, "name": meta.get("filename", ""),
                    "status": "updated" if changed else "current",
                    "reason": ", ".join(reason), "date": meta.get("date", "")}
        except Exception as e:
            return {"url": url, "name": meta.get("filename", ""),
                    "status": "error", "reason": str(e)[:80]}
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception as _close_exc:
                    logger.debug("Update-check response close failed: %s", _close_exc)

    try:
        safe_workers = max(1, min(32, int(max_workers)))
    except (TypeError, ValueError):
        safe_workers = 4
    with ThreadPoolExecutor(max_workers=safe_workers) as pool:
        futs = {pool.submit(_check, (i, e)): i for i, e in enumerate(entries)}
        done_n = 0
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                results.append(res)
            done_n += 1
            if progress_cb:
                progress_cb(done_n, total)
    return results


__all__ = ["_send_desktop_notification", "_check_for_app_updates", "_batch_check_updates"]
