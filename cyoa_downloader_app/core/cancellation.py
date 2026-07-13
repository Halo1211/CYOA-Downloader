"""Cancellation and progress-event bridge helpers.

This module owns the small mutable state used by the downloader and GUI worker
to publish progress events and interrupt retry/backoff sleeps. It intentionally
keeps the legacy private function names so the compatibility facade can continue
re-exporting them while the large legacy module shrinks.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from ..logging_setup import logger
from .progress import DownloadCancelledError

_PROGRESS_EVENT_SINK: Optional[Any] = None
_ACTIVE_CANCEL_EVENT: Optional[threading.Event] = None


def set_progress_event_sink(sink: Optional[Any], cancel_event: Optional[threading.Event] = None) -> None:
    """Install the active GUI/event sink and optional cancellation event."""
    global _PROGRESS_EVENT_SINK, _ACTIVE_CANCEL_EVENT
    _PROGRESS_EVENT_SINK = sink
    _ACTIVE_CANCEL_EVENT = cancel_event


def clear_progress_event_sink() -> None:
    """Clear progress sink and cancellation state after a run finishes."""
    set_progress_event_sink(None, None)


def _emit_progress_event(event_type: str, **payload: Any) -> None:
    sink = _PROGRESS_EVENT_SINK
    if sink is None:
        return
    event = {"type": str(event_type), "time": time.monotonic(), **payload}
    try:
        sink(event)
    except Exception as exc:
        logger.debug(f"Progress event sink rejected {event_type}: {exc}")


def _cancel_requested() -> bool:
    return bool(_ACTIVE_CANCEL_EVENT is not None and _ACTIVE_CANCEL_EVENT.is_set())


def _raise_if_cancelled() -> None:
    if _cancel_requested():
        raise DownloadCancelledError("Download cancelled by user")


def _cancel_aware_sleep(seconds: float) -> None:
    """Sleep interruptibly when a GUI cancellation event is active."""
    delay = max(0.0, float(seconds or 0.0))
    event = _ACTIVE_CANCEL_EVENT
    if event is None:
        time.sleep(delay)
        return
    if event.wait(delay):
        raise DownloadCancelledError("Download cancelled during retry/backoff")


__all__ = [
    "_PROGRESS_EVENT_SINK",
    "_ACTIVE_CANCEL_EVENT",
    "set_progress_event_sink",
    "clear_progress_event_sink",
    "_emit_progress_event",
    "_cancel_requested",
    "_raise_if_cancelled",
    "_cancel_aware_sleep",
]
