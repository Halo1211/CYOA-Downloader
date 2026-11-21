"""Pump browser events through cancellable, bounded slices of settling time."""

from __future__ import annotations

from ..core.cancellation import _raise_if_cancelled


def wait_for_runtime(page, milliseconds, *, after_poll=None):
    """Preserve the full settling budget while checking cancellation every 50 ms."""
    remaining = max(0, int(milliseconds))
    _raise_if_cancelled()
    while remaining:
        delay = min(50, remaining)
        page.wait_for_timeout(delay)
        _raise_if_cancelled()
        if after_poll is not None:
            after_poll()
        _raise_if_cancelled()
        remaining -= delay
