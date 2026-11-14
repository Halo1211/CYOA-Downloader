"""Regressions for browser settling and Tk responsiveness under queue load."""

import queue
from types import SimpleNamespace

from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.network import runtime_capture
from cyoa_downloader_app.project import discover


def test_progress_poll_yields_before_slow_callbacks_freeze_tk(monkeypatch):
    elapsed = [0.0]
    commands = queue.SimpleQueue()
    completed = []
    scheduled = []

    def callback():
        elapsed[0] += 0.003
        completed.append(True)

    for _ in range(100):
        commands.put(callback)
    gui = SimpleNamespace(
        root=SimpleNamespace(winfo_exists=lambda: True, after=lambda ms, fn: scheduled.append(ms)),
        _v46_ui_commands=commands,
        _v46_progress_queue=queue.Queue(),
        _v46_priority_progress_queue=queue.SimpleQueue(),
        _v46_telemetry=SimpleNamespace(snapshot=lambda: {}),
        _v46_render_progress=lambda snapshot: None,
        _v46_poll_progress=lambda: None,
    )
    monkeypatch.setattr(final_behaviors.time, "monotonic", lambda: elapsed[0])
    final_behaviors._v46_poll_progress(gui)
    assert elapsed[0] <= 0.012
    assert 0 < len(completed) < 100
    assert scheduled == [50]


def test_interaction_wait_finishes_early_only_after_stable_progress(monkeypatch):
    elapsed = [0]
    observed = {}
    state = {"pending": {"asset"}}

    def wait(ms):
        elapsed[0] += ms
        if elapsed[0] >= 500:
            observed["image.png"] = "image/png"
            state["pending"].clear()

    monkeypatch.setattr(runtime_capture, "_dom_progress_signature", lambda page: "next gate")
    monkeypatch.setattr(runtime_capture, "_candidate_snapshot", lambda page: [{"text": "Continue"}])
    runtime_capture._wait_for_interaction_progress(
        SimpleNamespace(wait_for_timeout=wait), observed, state, (0, "old gate"), 1800
    )
    assert 500 <= elapsed[0] < 1000


def test_interaction_wait_keeps_full_budget_for_delayed_gates(monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(runtime_capture, "_dom_progress_signature", lambda page: "unchanged")
    runtime_capture._wait_for_interaction_progress(
        SimpleNamespace(wait_for_timeout=lambda ms: elapsed.__setitem__(0, elapsed[0] + ms)),
        {},
        {},
        (0, "unchanged"),
        1800,
    )
    assert elapsed[0] == 1800


def test_final_gate_keeps_settling_for_delayed_assets(monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(runtime_capture, "_dom_progress_signature", lambda page: "story opened")
    monkeypatch.setattr(runtime_capture, "_candidate_snapshot", lambda page: [])
    runtime_capture._wait_for_interaction_progress(
        SimpleNamespace(wait_for_timeout=lambda ms: elapsed.__setitem__(0, elapsed[0] + ms)),
        {},
        {},
        (0, "gate"),
        1800,
    )
    assert elapsed[0] == 1800


def test_flat_page_does_not_repeat_settling_when_no_scroll_occurred():
    waits = []
    page = SimpleNamespace(
        evaluate=lambda *args: {"top": 0, "height": 600, "viewport": 800},
        wait_for_timeout=waits.append,
    )
    assert runtime_capture._incremental_scroll(page, 1800, 100) == 0
    assert waits == []


def test_non_cafe_project_discovery_does_not_fetch_page_twice(monkeypatch):
    extra_requests = []
    monkeypatch.setattr(discover, "fetch_response", lambda url, **kwargs: extra_requests.append(url))
    monkeypatch.setattr(discover, "get_source", lambda url: '<div id="app"></div>')
    monkeypatch.setattr(discover, "try_project_candidate", lambda url, **kwargs: ('{"rows":[]}', url))
    assert discover.get_project_source("https://example.test/game/", ai_mode="off")[0]
    assert extra_requests == []
