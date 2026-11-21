"""The startup probe observes real lifecycle boundaries only when opted in."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cyoa_downloader_app.diagnostics import startup


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(startup, "_active", None)
    for name in ("CYOA_STARTUP_REPORT", "CYOA_STARTUP_TOKEN", "CYOA_STARTUP_EXIT", "CYOA_STARTUP_STARTED_NS"):
        monkeypatch.delenv(name, raising=False)


def test_disabled_probe_performs_no_work(monkeypatch):
    writer = Mock(side_effect=AssertionError("disabled probe wrote a report"))
    monkeypatch.setattr(startup.json, "dumps", writer)
    startup.mark("entry")
    assert startup.probe() is None
    writer.assert_not_called()


def test_probe_requires_report_and_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CYOA_STARTUP_REPORT", str(tmp_path / "report.json"))
    assert startup.probe() is None
    monkeypatch.setenv("CYOA_STARTUP_TOKEN", "fixture")
    assert startup.probe().close is False


@pytest.mark.parametrize("stamp", ["invalid", "-1", "999999999999999999999999"])
def test_invalid_start_timestamp_falls_back(monkeypatch, tmp_path, stamp):
    monkeypatch.setenv("CYOA_STARTUP_REPORT", str(tmp_path / "report.json"))
    monkeypatch.setenv("CYOA_STARTUP_TOKEN", "fixture")
    monkeypatch.setenv("CYOA_STARTUP_STARTED_NS", stamp)
    before = startup.time.perf_counter_ns()
    assert startup.probe().started_ns >= before


def test_virtual_event_waits_for_both_map_and_ui_construction(tmp_path):
    path = tmp_path / "startup.json"
    probe = startup.StartupProbe(str(path), "fixture", startup.time.perf_counter_ns(), close=True)
    bindings, idle, scheduled = {}, [], []
    root = SimpleNamespace(bind=lambda name, fn, **_kw: bindings.update({name: fn}), after_idle=idle.append,
                           after=lambda delay, fn: scheduled.append(fn), destroy=Mock())
    root.event_generate = lambda name, **_kw: bindings[name](None)
    probe.attach(root)
    bindings["<Map>"](SimpleNamespace(widget=object()))
    probe.constructed()
    assert not idle and not path.exists()
    bindings["<Map>"](SimpleNamespace(widget=root))
    assert len(idle) == 1
    bindings["<Map>"](SimpleNamespace(widget=root))
    assert len(idle) == 1
    idle.pop()()
    result = json.loads(path.read_text("utf-8"))
    assert result["token"] == "fixture"
    assert result["seconds"]["input_event_ready"] >= result["seconds"]["window_mapped"]
    assert len(scheduled) == 1
    scheduled.pop()()
    root.destroy.assert_called_once()


def test_probe_does_not_close_normal_gui_after_report(tmp_path):
    root = SimpleNamespace(after=Mock())
    probe = startup.StartupProbe(str(tmp_path / "startup.json"), "fixture", startup.time.perf_counter_ns())
    probe.root = root
    probe._ready()
    root.after.assert_not_called()
