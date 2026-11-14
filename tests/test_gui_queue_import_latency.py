"""Queue imports must not parse or construct every row in one Tk callback."""

import queue
import threading
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.gui import app, final_behaviors, queue_import


class FakeRoot:
    def __init__(self):
        self.pending = {}
        self.sequence = 0

    def after(self, delay, callback):
        self.sequence += 1
        self.pending[self.sequence] = callback
        return self.sequence

    def after_cancel(self, key):
        self.pending.pop(key, None)

    def pump(self):
        while self.pending:
            key = next(iter(self.pending))
            self.pending.pop(key)()


def make_gui(clock):
    owner = threading.get_ident()
    root = FakeRoot()
    posted = queue.SimpleQueue()
    gui = SimpleNamespace(
        root=root,
        _mode_var="website_folder",
        _language="en",
        _is_running=False,
        _queue_rows=[],
        _queue_data=[],
        _run_on_ui_thread=posted.put,
        _set_status=lambda message: None,
    )
    for name in ("_import_button", "_export_button", "_add_btn", "_dl_btn"):
        button = SimpleNamespace(state="normal")
        button.configure = lambda state, current=button: setattr(current, "state", state)
        setattr(gui, name, button)

    def make_row(url, mode, filename):
        assert threading.get_ident() == owner
        assert len(gui._queue_data) == len(gui._queue_rows) + 1
        gui._queue_rows.append((url, mode, filename))
        clock[0] += 0.004

    gui._make_queue_row = make_row
    gui._status_messages = []
    gui._set_status = gui._status_messages.append
    return gui, posted


def test_import_parses_away_from_tk_and_posts_rows_to_ui(monkeypatch):
    clock = [0.0]
    gui, posted = make_gui(clock)
    owner = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()

    def reader(path):
        assert threading.get_ident() != owner, "File parser ran in the Tk thread"
        entered.set()
        assert release.wait(2)
        return [{"url": f"https://example.test/{i}", "filename": str(i)} for i in range(10)]

    monkeypatch.setattr(app, "import_queue_items_from_source", reader)
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", lambda **kwargs: "queue.csv")
    monkeypatch.setattr(queue_import, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    app.CYOADownloaderGUI._import_list(gui)
    try:
        assert entered.wait(1)
        assert gui._queue_import_busy
        assert not gui._queue_data
        task = gui._queue_import_task
        release.set()
        task.thread.join(2)
        posted.get_nowait()()
        assert 0 < len(gui._queue_rows) < 10
        gui.root.pump()
        assert [row["filename"] for row in gui._queue_data] == [str(i) for i in range(10)]
        assert len({row["_queue_id"] for row in gui._queue_data}) == 10
        assert not gui._queue_import_busy
        assert gui._dl_btn.state == "normal"
    finally:
        release.set()
        queue_import.cancel_queue_import(gui)


def test_cancelled_import_does_not_reappear_when_parser_finishes(monkeypatch):
    clock = [0.0]
    gui, _posted = make_gui(clock)
    task = queue_import.QueueImport(gui, "queue.csv")
    queue_import.cancel_queue_import(gui)
    task.accept([{"url": "https://example.test/late"}])
    assert gui._queue_data == []
    assert not gui._queue_import_busy
    assert not gui.root.pending


def test_row_slices_keep_mode_and_restore_controls_for_current_run(monkeypatch):
    clock = [0.0]
    gui, _posted = make_gui(clock)
    monkeypatch.setattr(queue_import, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    gui._is_running = True
    task = queue_import.QueueImport(gui, "queue.csv")
    task.accept([{"url": f"https://example.test/{i}"} for i in range(10)])
    assert clock[0] <= 0.010
    assert 0 < len(gui._queue_rows) < 10
    gui._mode_var = "auto"
    gui._is_running = False
    gui.root.pump()
    assert all(row["mode"] == "website_folder" for row in gui._queue_data)
    assert gui._dl_btn.state == "normal"
    assert not gui._queue_import_busy
    assert gui._status_messages[-1].startswith("Imported 10")


def test_download_waits_until_import_is_complete():
    final_behaviors._v46_start(SimpleNamespace(_is_running=False, _queue_import_busy=True))


@pytest.mark.parametrize("error", [OSError("fixture read failure"), DownloadCancelledError("cancelled")])
def test_parser_failure_or_cancellation_releases_import_controls(error):
    gui, posted = make_gui([0.0])

    def reader(path):
        raise error

    queue_import.begin_queue_import(gui, "queue.csv", reader)
    task = gui._queue_import_task
    task.thread.join(2)
    posted.get_nowait()()
    assert not gui._queue_import_busy
    assert gui._dl_btn.state == "normal"
    assert not gui._queue_data


def test_clear_cancels_remaining_slices(monkeypatch):
    clock = [0.0]
    gui, _posted = make_gui(clock)
    gui._update_queue_count = lambda: None
    monkeypatch.setattr(queue_import, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    queue_import.QueueImport(gui, "queue.csv").accept([{"url": f"https://example.test/{i}"} for i in range(10)])
    assert gui._queue_data and gui.root.pending
    app.CYOADownloaderGUI._clear_queue(gui)
    gui.root.pump()
    assert not gui._queue_data
    assert not gui._queue_rows
    assert not gui._queue_import_busy
