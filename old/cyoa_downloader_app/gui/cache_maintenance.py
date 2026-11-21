"""One bounded maintenance task shared by cache views in a GUI instance."""

from __future__ import annotations

import threading
from tkinter import TclError

from ..logging_setup import logger

_WIDGET_ERRORS = (TclError, RuntimeError)
# Storage/settings adapters can raise backend-specific exceptions. Report the
# failure on Tk's thread and release the busy state for all surviving views.
_OPERATION_ERRORS = (Exception,)


class CacheView:
    def __init__(self, service, owner, render, controls):
        self.service = service
        self.owner = owner
        self.render = render
        self.controls = controls
        self.active = True
        owner.bind("<Destroy>", self._destroyed, add="+")

    @property
    def ready(self):
        return self.active and not self.service.busy and not self.service.closed

    def _destroyed(self, event):
        if event.widget is self.owner:
            self.close()

    def close(self):
        self.active = False
        self.service.views.discard(self)

    def exists(self):
        if not self.active:
            return False
        try:
            if self.owner.winfo_exists():
                return True
        except _WIDGET_ERRORS:
            pass
        self.close()
        return False

    def start(self, action, operation):
        return self.service.start(action, operation) if self.ready else False


class CacheMaintenance:
    def __init__(self, post):
        self.post = post
        self.views = set()
        self.busy = False
        self.closed = False
        self.thread = None
        self._generation = 0
        self._lock = threading.Lock()

    def attach(self, owner, render, controls):
        view = CacheView(self, owner, render, controls)
        self.views.add(view)
        controls(self.busy)
        return view

    def _each_view(self, callback):
        for view in tuple(self.views):
            if view.exists():
                try:
                    callback(view)
                except _WIDGET_ERRORS:
                    view.close()

    def start(self, action, operation):
        if self.closed or self.busy:
            return False
        self.busy = True
        self._generation += 1
        generation = self._generation
        self._each_view(lambda view: view.controls(True))

        def work():
            result = error = None
            try:
                result = operation()
            except _OPERATION_ERRORS as exc:
                error = str(exc) or type(exc).__name__
                logger.warning("Cache maintenance %s failed: %s", action, exc)
            with self._lock:
                if self.closed:
                    self.busy = False
                    return
            self.post(lambda: self._finish(generation, action, result, error))

        self.thread = threading.Thread(target=work, name="cyoa-cache-maintenance", daemon=True)
        try:
            self.thread.start()
        except (OSError, RuntimeError) as exc:
            self._finish(generation, action, None, str(exc) or type(exc).__name__)
        return True

    def _finish(self, generation, action, result, error):
        if self.closed or generation != self._generation:
            return
        self.busy = False
        self._each_view(lambda view: view.controls(False))
        self._each_view(lambda view: view.render(action, result, error))

    def close(self):
        """Detach Tk clients; the current disk transaction is allowed to finish."""
        with self._lock:
            self.closed = True
        for view in tuple(self.views):
            view.close()

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()


def cache_maintenance(gui):
    service = getattr(gui, "_cache_maintenance", None)
    if service is None:
        service = CacheMaintenance(gui._run_on_ui_thread)
        gui._cache_maintenance = service
    return service
