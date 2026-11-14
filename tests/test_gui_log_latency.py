"""Keep formatted log bursts responsive without dropping records or styles."""

import queue
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.gui import final_behaviors, logging_ui


class TextBridge:
    """A deterministic slow Tcl boundary; time advances per native call."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def winfo_exists(self):
        return True

    def configure(self, **kwargs):
        pass

    def insert(self, index, *values):
        self.clock[0] += 0.001
        self.calls.append(values)

    def index(self, index):
        return "100.0"

    def see(self, index):
        pass


def test_formatted_burst_does_not_cross_tcl_for_every_color_segment(monkeypatch):
    clock = [0.0]
    text = TextBridge(clock)
    logs = queue.Queue()
    for i in range(150):
        logs.put(f"2026-11-14 06:00:00,000 - INFO - Saved asset {i}.webp")
    delays = []
    gui = SimpleNamespace(
        root=SimpleNamespace(winfo_exists=lambda: True, after=lambda delay, callback: delays.append(delay)),
        _log_txt=text,
        _log_queue=logs,
        _poll_log=lambda: None,
    )
    monkeypatch.setattr(logging_ui, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    logging_ui._v465_poll_log(gui)
    assert clock[0] <= 0.012
    pieces = [value for call in text.calls for value in call]
    assert sum(isinstance(value, str) and "Saved asset" in value for value in pieces) == 150
    assert pieces.count("TIMESTAMP") == 150
    assert pieces.count("LEVEL_INFO") == 150


def test_slow_log_widget_leaves_unrendered_records_queued(monkeypatch):
    clock = [0.0]
    text = TextBridge(clock)
    native_insert = text.insert

    def slow_insert(*args):
        native_insert(*args)
        clock[0] += 0.009

    text.insert = slow_insert
    logs = queue.Queue()
    for number in range(1000):
        logs.put(f"message {number}")
    delays = []
    gui = SimpleNamespace(
        root=SimpleNamespace(winfo_exists=lambda: True, after=lambda delay, callback: delays.append(delay)),
        _log_txt=text,
        _log_queue=logs,
        _poll_log=lambda: None,
    )
    monkeypatch.setattr(logging_ui, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    logging_ui._v465_poll_log(gui)
    assert clock[0] <= 0.020
    assert 0 < logs.qsize() < 1000
    assert delays[0] <= 50


def test_progress_burst_uses_available_time_without_starving_important_events(monkeypatch):
    clock = [0.0]
    events = queue.Queue()
    urgent = queue.SimpleQueue()
    received = []
    for _ in range(600):
        events.put({"type": "file_completed"})
    urgent.put({"type": "job_failed", "error": "fixture"})

    def apply(event):
        received.append(event)
        clock[0] += 0.000005

    gui = SimpleNamespace(
        root=SimpleNamespace(winfo_exists=lambda: True, after=lambda *args: None),
        _v46_ui_commands=queue.SimpleQueue(),
        _v46_progress_queue=events,
        _v46_priority_progress_queue=urgent,
        _v46_telemetry=SimpleNamespace(apply=apply, snapshot=lambda: {}),
        _v46_render_progress=lambda snapshot: None,
        _v46_poll_progress=lambda: None,
    )
    monkeypatch.setattr(final_behaviors, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    final_behaviors._v46_poll_progress(gui)
    assert events.empty()
    assert urgent.empty()
    assert len(received) == 601
    assert received[-1]["type"] == "job_failed"
    assert clock[0] <= 0.012


@pytest.mark.parametrize("important", [False, True])
def test_progress_draw_coalesces_bursts_but_keeps_the_final_frame(monkeypatch, important):
    clock = [0.0]
    events = queue.Queue()
    applied = []
    frames = []
    gui = SimpleNamespace(
        root=SimpleNamespace(winfo_exists=lambda: True, after=lambda *args: None),
        _v46_ui_commands=queue.SimpleQueue(),
        _v46_progress_queue=events,
        _v46_priority_progress_queue=queue.SimpleQueue(),
        _v46_telemetry=SimpleNamespace(apply=applied.append, snapshot=lambda: len(applied)),
        _v46_render_progress=frames.append,
        _v46_poll_progress=lambda: None,
    )
    monkeypatch.setattr(final_behaviors, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    events.put({"type": "file_completed"})
    final_behaviors._v46_poll_progress(gui)
    clock[0] = 0.01
    events.put({"type": "job_failed" if important else "file_completed"})
    final_behaviors._v46_poll_progress(gui)
    assert frames == ([1, 2] if important else [1])
    clock[0] = 0.16
    final_behaviors._v46_poll_progress(gui)
    assert frames == [1, 2]
    assert gui._v46_progress_render_pending is False
