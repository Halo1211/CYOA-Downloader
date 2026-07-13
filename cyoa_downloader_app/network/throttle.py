"""HTTP/2 switch, bandwidth throttle, and per-domain backoff/rate limiting.

Phase 64 moves the live throttle/backoff state reads to ``runtime.state``.  The
legacy facade is mirrored only when compatibility-visible globals change.
"""

from __future__ import annotations

import random
import time
from urllib.parse import urlparse

from ..core.cancellation import _cancel_aware_sleep
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy


def _set_http2_enabled(enabled: bool) -> None:
    """Enable/disable optional HTTP/2 fetches. Falls back to requests if httpx is missing."""
    state._HTTP2_ENABLED = bool(enabled)
    if state._HTTP2_ENABLED:
        try:
            import httpx  # type: ignore  # noqa: F401
            logger.info("HTTP/2 enabled for deep-scan fetches via httpx.")
        except Exception as e:
            state._HTTP2_ENABLED = False
            logger.warning(f"HTTP/2 requested but httpx is unavailable: {e}. Install: pip install httpx[http2]")
    else:
        logger.info("HTTP/2 disabled; using requests.")
    mirror_to_legacy("_HTTP2_ENABLED", state._HTTP2_ENABLED)


def _throttle_bandwidth(bytes_downloaded: int, *, record_gui: bool = True) -> None:
    """Sleep if necessary to keep download rate under the configured KB/s limit."""
    if record_gui and state._gui_speed_cb is not None:
        try:
            state._gui_speed_cb(bytes_downloaded)
        except Exception as exc:
            logger.debug("Ignored recoverable exception in _throttle_bandwidth: %s", exc)
    limit = state._bandwidth_limit_kbps
    if limit <= 0:
        return
    limit_bps = limit * 1024
    with state._bw_lock:
        now = time.monotonic()
        if state._bw_last_time == 0.0:
            state._bw_last_time = now
            mirror_to_legacy("_bw_last_time", state._bw_last_time)
        elapsed = now - state._bw_last_time
        state._bw_bytes_this_window += bytes_downloaded
        expected_time = state._bw_bytes_this_window / limit_bps
        sleep_for = expected_time - elapsed
        if sleep_for > 0:
            _cancel_aware_sleep(sleep_for)
            state._bw_last_time = time.monotonic()
            state._bw_bytes_this_window = 0
        elif elapsed > 1.0:
            state._bw_last_time = now
            state._bw_bytes_this_window = 0
        mirror_to_legacy("_bw_last_time", state._bw_last_time)
        mirror_to_legacy("_bw_bytes_this_window", state._bw_bytes_this_window)


def _domain_record_success(url: str) -> None:
    """Domain replied OK; halve the backoff."""
    try:
        domain = urlparse(url).netloc
        if not domain:
            return
        with state._domain_backoff_lock:
            if domain in state._domain_backoff:
                state._domain_backoff[domain] = max(0.0, state._domain_backoff[domain] / 2)
                state._domain_fail_count[domain] = max(0, state._domain_fail_count.get(domain, 0) - 1)
    except Exception as exc:
        logger.debug("Ignored recoverable exception in _domain_record_success: %s", exc)


def _domain_throttle(url: str) -> None:
    """Reserve a per-domain request slot, then wait without holding global locks."""
    try:
        domain = (urlparse(url).netloc or "").lower()
        if not domain:
            return
        now = time.monotonic()
        with state._domain_backoff_lock:
            backoff = max(0.0, float(state._domain_backoff.get(domain, 0.0) or 0.0))
        with state._domain_lock:
            previous_slot = float(state._domain_last_request.get(domain, 0.0) or 0.0)
            reserved_at = max(
                now,
                previous_slot + max(0.0, float(state._domain_min_interval)),
                now + backoff,
            )
            state._domain_last_request[domain] = reserved_at
        wait = reserved_at - now
        if wait > 0:
            _cancel_aware_sleep(wait)
    except DownloadCancelledError:
        raise
    except Exception as exc:
        logger.debug(f"Domain throttle failed for {url}: {exc}")


def _domain_record_failure(url: str, status: int = 0) -> float:
    """Increase bounded per-domain backoff and log internal failures."""
    try:
        domain = (urlparse(url).netloc or "").lower()
        if not domain:
            return 0.0
        with state._domain_backoff_lock:
            fails = state._domain_fail_count.get(domain, 0) + 1
            state._domain_fail_count[domain] = fails
            current = float(state._domain_backoff.get(domain, 0.0) or 0.0)
            new_backoff = state._BACKOFF_BASE if current <= 0.0 else min(current * 2.0, state._BACKOFF_MAX)
            jitter = new_backoff * state._BACKOFF_JITTER * (2.0 * random.random() - 1.0)
            new_backoff = max(0.1, min(state._BACKOFF_MAX, new_backoff + jitter))
            state._domain_backoff[domain] = new_backoff
        logger.debug(f"Backoff [{domain}] fails={fails} → {new_backoff:.1f}s (status={status})")
        return new_backoff
    except Exception as exc:
        logger.debug(f"Could not update domain backoff for {url}: {exc}")
        return 0.0
