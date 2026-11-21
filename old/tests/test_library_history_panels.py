"""Window task lifecycle and cancelled history transports."""

import threading
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.diagnostics import updates
from cyoa_downloader_app.gui.panel_jobs import PanelTask


class Owner:
    def __init__(self):
        self.owner_thread = threading.get_ident()
        self.callbacks = {}
        self.serial = 0

    def bind(self, event, callback, add):
        self.destroyed = callback

    def after(self, delay, callback):
        assert threading.get_ident() == self.owner_thread
        self.serial += 1
        self.callbacks[self.serial] = callback
        return self.serial

    def after_cancel(self, identity):
        assert threading.get_ident() == self.owner_thread
        self.callbacks.pop(identity, None)

    def pump(self):
        callbacks, self.callbacks = self.callbacks, {}
        for callback in callbacks.values():
            callback()

    def destroy(self):
        self.destroyed(SimpleNamespace(widget=self))


def test_storage_work_and_progress_never_enter_tk_and_reports_coalesce():
    owner = Owner()
    task = PanelTask(owner)
    done, progress = [], []
    entered, release = threading.Event(), threading.Event()
    def work(cancel, report):
        assert threading.get_ident() != owner.owner_thread
        for i in range(10000):
            report(i, 10000)
        entered.set()
        assert release.wait(2)
        return ["record"]
    try:
        assert task.start(work, lambda *r: done.append(r), lambda *r: progress.append(r))
        assert entered.wait(1)
        assert not task.start(lambda *_: pytest.fail("duplicate read"), lambda *_: None)
        assert len(owner.callbacks) == 1 and not progress and not done
        owner.pump()
        assert progress == [(9999, 10000)]
        release.set()
        task.thread.join(2)
        owner.pump()
        assert done == [(["record"], None)]
        assert not task.busy and not owner.callbacks
    finally:
        release.set()
        task.thread.join(2)
        task.close()


def test_destroy_discards_late_results_and_cancels_scheduled_poll():
    owner = Owner()
    task = PanelTask(owner)
    entered, release = threading.Event(), threading.Event()
    events = []
    def work(cancel, report):
        entered.set()
        assert release.wait(2)
        events.append(cancel.is_set())
        return "late"
    try:
        task.start(work, lambda *_: pytest.fail("closed view updated"))
        assert entered.wait(1)
        owner.destroy()
        assert not owner.callbacks and task.closed
        release.set()
        task.thread.join(2)
        owner.pump()
        assert events == [True] and task.result is None
        assert not task.start(work, lambda *_: None)
    finally:
        release.set()
        task.thread.join(2)


def test_failure_can_be_retried_and_cancellation_is_contained():
    owner = Owner()
    task = PanelTask(owner)
    errors = []
    def fail(*_):
        raise OSError("unreadable database")
    task.start(fail, lambda value, error: errors.append(error))
    task.thread.join(2)
    owner.pump()
    assert isinstance(errors[0], OSError) and not task.busy
    task.start(lambda *_: [], lambda value, error: errors.append(error))
    task.thread.join(2)
    owner.pump()
    assert errors[-1] is None
    task.close()


def test_cancel_before_history_check_does_not_open_transports(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(updates, "fetch_response", lambda *a, **k: pytest.fail("cancelled request"))
    with pytest.raises(DownloadCancelledError):
        updates._batch_check_updates({"https://example.invalid/": {"success": True}}, cancel_event=cancel)


def test_cancel_history_stops_queued_probes_and_closes_active_response(monkeypatch):
    cancel = threading.Event()
    started, closed = [], []
    class Response:
        status_code = 200
        def __init__(self):
            self.headers = {}
        def close(self):
            closed.append(True)
    def fetch(url, **kwargs):
        started.append(url)
        cancel.set()
        return Response()
    monkeypatch.setattr(updates, "fetch_response", fetch)
    history = {f"https://example.invalid/{i}": {"success": True} for i in range(1000)}
    with pytest.raises(DownloadCancelledError):
        updates._batch_check_updates(history, max_workers=1, cancel_event=cancel)
    assert len(started) == len(closed) == 1
