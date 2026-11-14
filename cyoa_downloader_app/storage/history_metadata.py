"""One daemon owner and a bounded, coalesced queue for optional history probes."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from ..logging_setup import logger

_HISTORY_WORKER_ERRORS = (Exception,)


@dataclass
class _Job:
    key: tuple
    action: object
    owner: object = None
    cancelled: threading.Event = field(default_factory=threading.Event)


class HistoryMetadataQueue:
    def __init__(self, max_pending=32):
        self.max_pending = max(1, int(max_pending))
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._active = self._thread = None
        self._closed = False

    def submit(self, key, action, *, owner=None):
        with self._condition:
            if self._closed or (key not in self._pending and len(self._pending) >= self.max_pending):
                return False
            previous = self._pending.get(key)
            if previous:
                previous.cancelled.set()
            if self._active is not None and self._active.key == key:
                self._active.cancelled.set()
            self._pending[key] = _Job(key, action, owner)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="cyoa-history-metadata", daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return True

    def cancel_owner(self, owner):
        with self._condition:
            jobs = list(self._pending.values())
            if self._active is not None:
                jobs.append(self._active)
            for job in jobs:
                if job.owner is owner:
                    job.cancelled.set()
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                while not self._pending:
                    if self._closed:
                        return
                    self._condition.wait()
                _, job = self._pending.popitem(last=False)
                self._active = job
            try:
                if job.owner is not None and job.owner.is_set():
                    job.cancelled.set()
                job.action(job.cancelled)
            except _HISTORY_WORKER_ERRORS:
                # Optional independently implemented transports are a boundary;
                # one failed probe must not kill this owner or the GUI queue.
                logger.exception("Background history metadata failed")
            finally:
                with self._condition:
                    self._active = None
                    self._condition.notify_all()

    def wait_idle(self, timeout=10):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending or self._active is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def snapshot(self):
        with self._condition:
            return {"pending": len(self._pending), "active": int(self._active is not None), "limit": self.max_pending}

    def close(self):
        with self._condition:
            self._closed = True
            for job in self._pending.values():
                job.cancelled.set()
            if self._active is not None:
                self._active.cancelled.set()
            self._condition.notify_all()
