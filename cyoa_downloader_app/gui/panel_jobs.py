"""Window-owned read jobs. Workers exchange Python values, never Tk calls."""

from __future__ import annotations

import threading

from ..core.progress import DownloadCancelledError

# Storage and optional network backends expose their own exception classes.
_JOB_ERRORS = (Exception,)


class PanelTask:
    def __init__(self, owner):
        self.owner = owner
        self.busy = False
        self.closed = False
        self.after_id = None
        self.thread = None
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.result = None
        self.latest_progress = None
        self.done = self.progress = None
        owner.bind("<Destroy>", self._destroyed, add="+")

    def _destroyed(self, event):
        if event.widget is self.owner:
            self.close()

    def start(self, work, done, progress=None):
        if self.closed or self.busy:
            return False
        self.busy = True
        self.cancelled = threading.Event()
        self.done, self.progress = done, progress
        self.result = self.latest_progress = None

        def report(*value):
            if self.cancelled.is_set():
                raise DownloadCancelledError("Panel closed")
            with self.lock:
                self.latest_progress = value

        def run():
            try:
                value, error = work(self.cancelled, report), None
            except _JOB_ERRORS as exc:
                value, error = None, exc
            with self.lock:
                if not self.cancelled.is_set():
                    self.result = (value, error)

        self.thread = threading.Thread(target=run, daemon=True, name="cyoa-panel-read")
        self.thread.start()
        self.after_id = self.owner.after(20, self.poll)
        return True

    def poll(self):
        self.after_id = None
        if self.closed:
            return
        with self.lock:
            result, progress = self.result, self.latest_progress
            self.result = self.latest_progress = None
        if result is not None:
            done = self.done
            self.busy = False
            self.done = self.progress = None
            done(*result)
        else:
            if progress is not None and self.progress is not None:
                self.progress(*progress)
            self.after_id = self.owner.after(20, self.poll)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancelled.set()
        if self.after_id is not None:
            self.owner.after_cancel(self.after_id)
        self.after_id = None
        self.done = self.progress = None
        self.busy = False
        with self.lock:
            self.result = self.latest_progress = None
