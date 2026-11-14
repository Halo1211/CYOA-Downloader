"""Process-wide HTTP admission, shared by independent request/session owners."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlparse

from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError

HTTP_GLOBAL_LIMIT = 20
HTTP_HOST_LIMIT = 12
_REQUEST_SCOPE = ContextVar("cyoa_request_scope", default=(10, None))


@contextmanager
def request_scope(*, priority=10, cancel_event=None):
    """Set admission priority/cancellation for requests made in this thread."""
    token = _REQUEST_SCOPE.set((priority, cancel_event))
    try:
        yield
    finally:
        _REQUEST_SCOPE.reset(token)


@dataclass(eq=False)
class _Waiter:
    host: str
    priority: int
    ticket: int


class RequestLease:
    def __init__(self, budget, host):
        self.budget, self.host = budget, host
        self.released = False

    def release(self):
        with self.budget.condition:
            if self.released:
                return
            self.released = True
            self.budget.active -= 1
            self.budget.hosts[self.host] -= 1
            if not self.budget.hosts[self.host]:
                del self.budget.hosts[self.host]
            self.budget.condition.notify_all()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


class RequestBudget:
    def __init__(self, total=HTTP_GLOBAL_LIMIT, per_host=HTTP_HOST_LIMIT):
        self.total, self.per_host = max(1, int(total)), max(1, int(per_host))
        self.condition = threading.Condition()
        self.active = self.peak = self.serial = 0
        self.hosts = {}
        self.waiters = []

    def acquire(self, url, *, check_cancelled=None):
        host = (urlparse(url).hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
        priority, cancel_event = _REQUEST_SCOPE.get()
        def check():
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelledError("Request owner cancelled")
            (check_cancelled or _raise_if_cancelled)()

        with self.condition:
            self.serial += 1
            waiter = _Waiter(host, priority, self.serial)
            self.waiters.append(waiter)
            try:
                while True:
                    check()
                    if self.active < self.total and self.hosts.get(host, 0) < self.per_host:
                        # A saturated host cannot block another host's free slot.
                        eligible = (w for w in self.waiters if self.hosts.get(w.host, 0) < self.per_host)
                        first = min(eligible, key=lambda w: (w.priority, w.ticket))
                        if first is waiter:
                            self.active += 1
                            self.hosts[host] = self.hosts.get(host, 0) + 1
                            self.peak = max(self.peak, self.active)
                            return RequestLease(self, host)
                    self.condition.wait(.05)
            finally:
                self.waiters.remove(waiter)
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {"active": self.active, "peak": self.peak, "hosts": dict(self.hosts),
                    "waiting": len(self.waiters), "global_limit": self.total, "host_limit": self.per_host}


HTTP_BUDGET = RequestBudget()
