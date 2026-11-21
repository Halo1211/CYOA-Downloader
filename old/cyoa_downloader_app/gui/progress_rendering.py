"""Present telemetry only when visible; own one modest animation timer per GUI."""

from __future__ import annotations

from tkinter import TclError

_WIDGET_ERRORS = (AttributeError, RuntimeError, TclError)
ANIMATION_INTERVAL_MS = 80


class ProgressView:
    def __init__(self, gui):
        self.gui = gui
        self.values = {}
        self.animations = {}
        self.after_id = None
        self.generation = 0

    def window_visible(self):
        return getattr(self.gui, "_progress_window_mapped", True)

    def details_visible(self):
        return self.window_visible() and getattr(self.gui, "_v46_progress_expanded", True)

    def set(self, name, value):
        widget = getattr(self.gui, name)
        if self.matches(name, widget, value):
            return
        widget.set(value)
        self.values[name] = (widget, value)

    def matches(self, key, widget, value):
        old = self.values.get(key)
        return old is not None and old[0] is widget and old[1] == value

    def configure(self, name, **options):
        widget = getattr(self.gui, name)
        key = name + ":configure"
        if not self.matches(key, widget, options):
            widget.configure(**options)
            self.values[key] = (widget, options)

    def bar(self, name, value=0, *, animate=False):
        widget = getattr(self.gui, name)
        animate = animate and self.details_visible()
        mode = "indeterminate" if animate else "determinate"
        key = name + ":mode"
        old = self.values.get(key)
        if old is None or old[0] is not widget or old[1] != mode:
            widget.stop()
            # Four 20 ms steps per 80 ms frame preserve approximate animation
            # travel speed without four canvas redraws. Use only public CTk APIs.
            widget.configure(mode=mode, **({"indeterminate_speed": 4} if animate else {}))
            self.values[key] = (widget, mode)
        if animate:
            self.animations[name] = widget
            self._schedule()
        else:
            self.animations.pop(name, None)
            self.set(name, value)
            if not self.animations:
                self.halt()

    def _schedule(self):
        if self.animations and self.after_id is None:
            generation = self.generation
            self.after_id = self.gui.root.after(ANIMATION_INTERVAL_MS, lambda: self._tick(generation))

    def _tick(self, generation):
        if generation != self.generation:
            return
        self.after_id = None
        if not self.details_visible():
            self.halt()
            return
        for name, widget in tuple(self.animations.items()):
            try:
                widget.step()
            except _WIDGET_ERRORS:
                self.animations.pop(name, None)
        self._schedule()

    def halt(self):
        self.generation += 1
        if self.after_id is not None:
            try:
                self.gui.root.after_cancel(self.after_id)
            except _WIDGET_ERRORS:
                pass
            self.after_id = None
        for widget in self.animations.values():
            try:
                widget.stop()
            except _WIDGET_ERRORS:
                pass
        self.animations.clear()

    def reset(self):
        self.halt()
        self.values.clear()


def view(gui):
    current = getattr(gui, "_progress_view", None)
    if current is None:
        current = gui._progress_view = ProgressView(gui)
    return current


def refresh(gui):
    if getattr(gui, "_progress_view_ready", False):
        gui._v46_render_progress(gui._v46_telemetry.snapshot())


def install_visibility(gui):
    def changed(event):
        if event.widget is not gui.root:
            return
        mapped = bool(gui.root.winfo_viewable())
        if mapped != gui._progress_window_mapped:
            gui._progress_window_mapped = mapped
            if not mapped:
                view(gui).halt()
            refresh(gui)
    gui._progress_window_mapped = bool(gui.root.winfo_viewable())
    gui.root.bind("<Map>", changed, add="+")
    gui.root.bind("<Unmap>", changed, add="+")
