"""Opt-in startup phase measurements for isolated source/frozen benchmarks.

No probe, file I/O, event binding or automatic exit is enabled by default.
The benchmark supplies a unique report/token and may explicitly request exit.
"""

from __future__ import annotations

import json
import os
import time

_active = None


class StartupProbe:
    def __init__(self, report, token, started_ns, close=False):
        self.report, self.token, self.started_ns, self.close = report, token, started_ns, close
        self.phases = {}
        self.ui_complete = self.event_scheduled = False
        self.root = None

    def mark(self, name, at_ns=None):
        self.phases.setdefault(name, at_ns if at_ns is not None else time.perf_counter_ns())

    def attach(self, root):
        self.root = root
        self.mark("root_created")
        def mapped(event):
            if event.widget is root:
                self.mark("window_mapped")
                self._schedule()
        root.bind("<Map>", mapped, add="+")
        root.bind("<<CYOAStartupReady>>", self._ready, add="+")

    def constructed(self):
        self.mark("ui_constructed")
        self.ui_complete = True
        self._schedule()

    def _schedule(self):
        if self.ui_complete and "window_mapped" in self.phases and not self.event_scheduled:
            self.event_scheduled = True
            self.root.after_idle(lambda: self.root.event_generate("<<CYOAStartupReady>>", when="tail"))

    def _ready(self, _event=None):
        if "input_event_ready" in self.phases:
            return
        self.mark("input_event_ready")
        try:
            from ..core.atomic_io import atomic_write_text
            atomic_write_text(self.report, json.dumps({
                "token": self.token, "pid": os.getpid(), "started_ns": self.started_ns,
                "phases_ns": self.phases,
                "seconds": {name: (stamp - self.started_ns) / 1e9 for name, stamp in self.phases.items()},
                "readiness": "Tk virtual event delivered after main-window Map and UI construction",
            }, indent=2))
        except (OSError, TypeError, ValueError):
            # Diagnostics never turn an otherwise working GUI into a failure.
            pass
        finally:
            if self.close:
                self.root.after(100, self.root.destroy)


def probe():
    global _active
    report = os.environ.get("CYOA_STARTUP_REPORT", "")
    token = os.environ.get("CYOA_STARTUP_TOKEN", "")
    if not report or not token:
        return None
    if _active is None:
        try:
            started = int(os.environ.get("CYOA_STARTUP_STARTED_NS", "0"))
        except ValueError:
            started = 0
        now = time.perf_counter_ns()
        if started <= 0 or started > now:
            started = now
        _active = StartupProbe(report, token, started, os.environ.get("CYOA_STARTUP_EXIT") == "1")
    return _active


def mark(name, at_ns=None):
    current = probe()
    if current is not None:
        current.mark(name, at_ns)
