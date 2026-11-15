"""Exercise actual start/worker boundaries with plain values and no downloads."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.storage import resume


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def start_gui(tmp_path, monkeypatch):
    finished, captured = threading.Event(), {}
    ui_thread = threading.get_ident()
    gui = SimpleNamespace(
        _is_running=False,
        _queue_data=[
            {"url": "https://fixture.invalid/game", "filename": "First 雪", "mode": "", "_queue_id": "one"},
            {"url": "https://fixture.invalid/game", "filename": "Second", "mode": "zip", "_queue_id": "two"},
        ],
        _wait_var=Value("1"), _threads_var=Value("2"), _bw_var=Value("0"),
        _outdir_var=Value(str(tmp_path)), _prepare_ytdlp_cookies=lambda: True,
        _mode_var=Value("auto"), _cancel_event=threading.Event(), _paused=threading.Event(),
        _v46_telemetry=SimpleNamespace(reset=lambda _n: None),
        _v46_enqueue_progress=lambda _e: None, _dl_btn=Mock(), _pause_btn=Mock(),
        _v46_cancel_btn=Mock(), _v46_copy_error_btn=Mock(), _status_var=Value(""),
        _fonts_var=Value(False), _analyse_var=Value(False), _cf_mode_var=Value("off"),
        _http2_var=Value(False), _ytdlp_var=Value(False), _cyoa_mgr_var=Value(False),
        _show_results=lambda: None, _done=finished.set,
    )

    def sink():
        if threading.get_ident() != ui_thread:
            raise OSError("fixture: stop after snapshot preparation, before transport setup")

    def worker(*args):
        captured["args"] = args
        final_behaviors._v46_worker(gui, *args)

    gui._v46_set_event_sink = sink
    gui._worker = worker
    gui._set_status = gui._status_var.set
    gui._run_on_ui_thread = lambda callback: callback()
    monkeypatch.setattr(final_behaviors, "logger", Mock())
    yield gui, finished, captured
    if hasattr(gui, "_worker_thread"):
        gui._worker_thread.join(3)


def test_resume_identity_preparation_runs_off_the_start_callback(start_gui, monkeypatch):
    gui, finished, captured = start_gui
    ui_thread = threading.get_ident()
    threads = []
    native = resume.resume_job_key

    def key(*values):
        threads.append(threading.get_ident())
        return native(*values)

    monkeypatch.setattr(resume, "resume_job_key", key)
    final_behaviors._v46_start(gui)
    assert finished.wait(3)
    assert threads and all(value != ui_thread for value in threads), "resume hashing ran on the UI start callback"
    items, mode = captured["args"][:2]
    assert mode == "auto" and items[0]["_run_file_name"] == "First 雪"
    assert items[0]["mode"] == "auto"
    assert items[0]["_resume_key"] != items[1]["_resume_key"]


def test_queued_edits_reordering_and_new_rows_cannot_change_the_run_snapshot(start_gui, monkeypatch):
    gui, finished, captured = start_gui
    release = threading.Event()
    native_thread = threading.Thread

    class DelayedThread:
        def __init__(self, *, target, args, name, daemon):
            self.delegate = native_thread(target=lambda: (release.wait(3), target(*args)), name=name, daemon=daemon)

        def start(self):
            self.delegate.start()

        def join(self, timeout=None):
            self.delegate.join(timeout)

    monkeypatch.setattr(final_behaviors.threading, "Thread", DelayedThread)
    final_behaviors._v46_start(gui)
    gui._queue_data[0].update(filename="Edited", mode="both")
    gui._queue_data.reverse()
    gui._queue_data.append({"url": "https://fixture.invalid/new", "filename": "Late", "_queue_id": "late"})
    gui._mode_var.set("zip")
    release.set()
    assert finished.wait(3)
    items, default_mode = captured["args"][:2]
    assert len(items) == 2 and default_mode == "auto"
    assert [row["_run_file_name"] for row in items] == ["First 雪", "Second"]
    assert [row["mode"] for row in items] == ["auto", "zip"]
    assert gui._active_run_order == ("one", "two")
    assert items[0]["_resume_key"] == resume.resume_job_key("https://fixture.invalid/game", "First 雪", "auto")


def test_snapshot_preparation_cancellation_keeps_original_queue_values():
    from cyoa_downloader_app.core.progress import DownloadCancelledError
    from cyoa_downloader_app.gui.run_preparation import RunSnapshot

    original = [{"url": "one", "filename": ""}, {"url": "two", "filename": ""}]
    snapshot = RunSnapshot(original)
    cancel = threading.Event()

    def name(url):
        cancel.set()
        return url

    with pytest.raises(DownloadCancelledError):
        snapshot.prepare("auto", name, cancel)
    assert not snapshot.prepared
    assert original == [{"url": "one", "filename": ""}, {"url": "two", "filename": ""}]


def test_prepared_snapshot_does_not_recompute_identity_for_a_different_default():
    from cyoa_downloader_app.gui.run_preparation import RunSnapshot

    snapshot = RunSnapshot([{"url": "https://fixture.invalid/story", "filename": "", "mode": ""}])
    snapshot.prepare("auto", lambda _url: "Story", threading.Event())
    before = dict(snapshot[0])
    snapshot.prepare("zip", lambda _url: "Changed", threading.Event())
    assert snapshot[0] == before
