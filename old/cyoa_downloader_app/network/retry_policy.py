"""Let an outer media loop own status retries without changing shared clients."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from email.utils import parsedate_to_datetime

_CALLER_STATUS_RETRIES = ContextVar("cyoa_caller_status_retries", default=False)
MEDIA_STATUS_RETRY_CODES = frozenset({429, 500, 502, 503, 504})


@contextmanager
def caller_status_retry_scope():
    token = _CALLER_STATUS_RETRIES.set(True)
    try:
        yield
    finally:
        _CALLER_STATUS_RETRIES.reset(token)


def status_retries_owned_by_caller():
    return _CALLER_STATUS_RETRIES.get()


def retry_after_seconds(value, *, now=None):
    """Accept delta seconds and HTTP dates; malformed values add no delay."""
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError, OSError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0
