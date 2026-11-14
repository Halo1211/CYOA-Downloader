"""Settling preserves delayed work while cancellation and response draining run."""

import threading

import pytest

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.network import runtime_capture
from cyoa_downloader_app.network.runtime_waits import wait_for_runtime


class Page:
    def __init__(self, advance=None):
        self.waits = []
        self.advance = advance

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if self.advance:
            self.advance(sum(self.waits))


@pytest.mark.parametrize("budget", [0, 1, 49, 50, 51, 1800])
def test_full_budget_and_response_draining_slices(budget):
    page, drained = Page(), []
    wait_for_runtime(page, budget, after_poll=lambda: drained.append(sum(page.waits)))
    assert sum(page.waits) == budget
    assert all(0 < delay <= 50 for delay in page.waits)
    assert len(drained) == len(page.waits)


def test_cancel_during_wait_stops_before_drain_and_keeps_rest_of_budget_unspent():
    cancel = threading.Event()
    cancellation.set_progress_event_sink(None, cancel)
    drained = []
    page = Page(lambda elapsed: cancel.set() if elapsed >= 150 else None)
    try:
        with pytest.raises(DownloadCancelledError):
            wait_for_runtime(page, 1800, after_poll=lambda: drained.append(1))
    finally:
        cancellation.clear_progress_event_sink()
    assert sum(page.waits) == 150 and len(drained) == 2


def test_cancel_during_drain_stops_following_browser_waits():
    cancel = threading.Event()
    cancellation.set_progress_event_sink(None, cancel)
    page = Page()
    try:
        with pytest.raises(DownloadCancelledError):
            wait_for_runtime(page, 1800, after_poll=cancel.set)
    finally:
        cancellation.clear_progress_event_sink()
    assert page.waits == [50]


def test_delayed_gate_retains_full_budget(monkeypatch):
    page = Page()
    monkeypatch.setattr(runtime_capture, "_dom_progress_signature", lambda page: "unchanged")
    runtime_capture._wait_for_interaction_progress(page, {}, {"pending": set()}, (0, "unchanged"), 1800)
    assert sum(page.waits) == 1800


def test_last_gate_without_next_control_keeps_late_asset_window(monkeypatch):
    observed = {}
    page = Page(lambda elapsed: observed.update({"https://fixture.test/late.svg": "image/svg+xml"}) if elapsed >= 1450 else None)
    monkeypatch.setattr(runtime_capture, "_dom_progress_signature", lambda page: "changed")
    monkeypatch.setattr(runtime_capture, "_candidate_snapshot", lambda page: [])
    runtime_capture._wait_for_interaction_progress(page, observed, {"pending": set()}, (0, "before"), 1800)
    assert sum(page.waits) == 1800 and observed


def test_ready_next_gate_waits_for_real_pending_asset_and_drains(monkeypatch):
    pending, drained = {"asset"}, []
    page = Page(lambda elapsed: pending.clear() if elapsed >= 900 else None)
    monkeypatch.setattr(runtime_capture, "_dom_progress_signature", lambda page: "changed")
    monkeypatch.setattr(runtime_capture, "_candidate_snapshot", lambda page: [{"text": "Continue"}])
    runtime_capture._wait_for_interaction_progress(page, {},
        {"pending": pending, "after_poll": lambda: drained.append(sum(page.waits))}, (0, "before"), 1800)
    assert 900 <= sum(page.waits) < 1800
    assert drained and max(page.waits) <= 50


def test_cancel_during_scroll_return_sweep_is_not_swallowed():
    cancel = threading.Event()
    page = Page(lambda elapsed: cancel.set() if elapsed >= 350 else None)
    page.evaluate = lambda script, *args: {"top": 0, "height": 2000, "viewport": 1000}
    cancellation.set_progress_event_sink(None, cancel)
    try:
        with pytest.raises(DownloadCancelledError):
            runtime_capture._incremental_scroll(page, 1800, 1)
    finally:
        cancellation.clear_progress_event_sink()
    assert sum(page.waits) == 350
