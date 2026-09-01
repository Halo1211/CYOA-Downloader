import inspect
import queue
import threading
from types import SimpleNamespace

from cyoa_downloader_app.gui.app import CYOADownloaderGUI
from cyoa_downloader_app.gui.final_behaviors import (
    _v46_enqueue_progress,
    _v46_worker,
)


class _NoWorkerTkRoot:
    def after(self, *_args, **_kwargs):
        raise AssertionError("worker entered Tcl through root.after")


def _run_in_worker(callback):
    failure = []

    def run():
        try:
            callback()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - assertion aid
            failure.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not failure


def test_worker_status_update_uses_python_queue_not_tcl():
    values = []
    gui = SimpleNamespace(
        root=_NoWorkerTkRoot(),
        _v46_ui_commands=queue.SimpleQueue(),
        _v46_status_lock=threading.Lock(),
        _v46_pending_status=None,
        _v46_status_command_pending=False,
        _status_var=SimpleNamespace(set=values.append),
    )
    gui._run_on_ui_thread = lambda callback: CYOADownloaderGUI._run_on_ui_thread(gui, callback)

    _run_in_worker(lambda: CYOADownloaderGUI._set_status(gui, "Job 4 of 234"))

    assert values == []
    gui._v46_ui_commands.get_nowait()()
    assert values == ["Job 4 of 234"]


def test_worker_status_burst_is_coalesced_for_234_item_queue():
    values = []
    gui = SimpleNamespace(
        root=_NoWorkerTkRoot(),
        _v46_ui_commands=queue.SimpleQueue(),
        _v46_status_lock=threading.Lock(),
        _v46_pending_status=None,
        _v46_status_command_pending=False,
        _status_var=SimpleNamespace(set=values.append),
    )
    gui._run_on_ui_thread = lambda callback: CYOADownloaderGUI._run_on_ui_thread(gui, callback)

    def emit_batch():
        for job in range(1, 235):
            CYOADownloaderGUI._set_status(gui, f"Job {job} of 234")

    _run_in_worker(emit_batch)

    callback = gui._v46_ui_commands.get_nowait()
    assert gui._v46_ui_commands.empty()
    callback()
    assert values == ["Job 234 of 234"]


def test_234_critical_events_never_wait_for_saturated_progress_queue():
    normal_queue = queue.Queue(maxsize=1)
    normal_queue.put_nowait({"type": "existing_important_event"})
    priority_queue = queue.SimpleQueue()
    gui = SimpleNamespace(
        _v46_progress_queue=normal_queue,
        _v46_priority_progress_queue=priority_queue,
    )

    def emit_failures():
        for job in range(1, 235):
            _v46_enqueue_progress(
                gui, {"type": "job_failed", "error": f"job {job}"}
            )

    _run_in_worker(emit_failures)

    assert normal_queue.get_nowait() == {"type": "existing_important_event"}
    preserved = [priority_queue.get_nowait() for _ in range(234)]
    assert preserved[0]["error"] == "job 1"
    assert preserved[-1]["error"] == "job 234"
    assert priority_queue.empty()


def test_active_download_worker_contains_no_direct_tk_calls():
    source = inspect.getsource(_v46_worker)

    assert ".root.after" not in source
    assert "._status_var.set" not in source
    assert ".configure(" not in source
    assert "._run_on_ui_thread(self._show_results)" in source
    assert "._run_on_ui_thread(self._done)" in source


def test_worker_dot_update_uses_python_queue_not_tcl():
    calls = []

    class Dot:
        def winfo_exists(self):
            return True

        def delete(self, value):
            calls.append(("delete", value))

        def create_oval(self, *args, **kwargs):
            calls.append(("oval", args, kwargs))

    gui = SimpleNamespace(
        root=_NoWorkerTkRoot(),
        _v46_ui_commands=queue.SimpleQueue(),
        _queue_rows=[(None, Dot(), None, None, None)],
        _p=lambda: {"muted2": "#888888"},
    )
    gui._run_on_ui_thread = lambda callback: CYOADownloaderGUI._run_on_ui_thread(gui, callback)

    _run_in_worker(lambda: CYOADownloaderGUI._set_dot(gui, 0, "running"))

    assert calls == []
    gui._v46_ui_commands.get_nowait()()
    assert calls[-1][2]["fill"] == "#3b82f6"
