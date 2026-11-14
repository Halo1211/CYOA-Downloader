"""A bounded pool of editable queue rows, backed by stable item identities."""

from __future__ import annotations

import math
import time
import tkinter as tk
import uuid


class QueueIndex:
    def __init__(self, items):
        self.items = items
        self.positions = {}
        self.states = {}
        self.known = 0
        self.appended()

    def appended(self):
        for index in range(self.known, len(self.items)):
            item = self.items[index]
            identity = str(item.get("_queue_id") or "")
            if not identity or identity in self.positions:
                identity = uuid.uuid4().hex
            item["_queue_id"] = identity
            self.positions[identity] = index
        self.known = len(self.items)

    def rebuild(self):
        self.positions.clear()
        self.known = 0
        self.appended()
        self.states = {key: value for key, value in self.states.items() if key in self.positions}

    def get(self, identity):
        index = self.positions.get(identity)
        return self.items[index] if index is not None else None

    def swap(self, i, j):
        if min(i, j) < 0 or max(i, j) >= len(self.items):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        self.positions[self.items[i]["_queue_id"]] = i
        self.positions[self.items[j]["_queue_id"]] = j

    def remove_ids(self, identities):
        old_count = len(self.items)
        self.items[:] = [item for item in self.items if item["_queue_id"] not in identities]
        self.rebuild()
        return old_count - len(self.items)

    def clear(self):
        self.items.clear()
        self.positions.clear()
        self.states.clear()
        self.known = 0


class VirtualRow:
    def __init__(self, view):
        import customtkinter as ctk

        self.view = view
        self.identity = None
        self.snapshot = None
        self.changing = False
        p, fonts = view.gui._p(), view.fonts
        self.frame = ctk.CTkFrame(view.canvas, corner_radius=6, fg_color=p["surface"],
                                  border_width=1, border_color=p["border"])
        self.frame.grid_columnconfigure(2, weight=1)
        self.frame.grid_propagate(False)
        self.handle = ctk.CTkLabel(self.frame, text="⠿", width=18, font=fonts["handle"],
                                   text_color=p["muted2"], cursor="fleur")
        self.handle.grid(row=0, column=0, rowspan=2, padx=(6, 2), pady=8)
        self.dot = tk.Canvas(self.frame, width=10, height=10, highlightthickness=0, bg=p["surface"])
        self.dot.grid(row=0, column=1, padx=(2, 6), pady=8)
        self.url = ctk.CTkLabel(self.frame, text="", font=fonts["url"], text_color=p["muted"], anchor="w")
        self.url.grid(row=0, column=2, sticky="ew", padx=4, pady=(6, 1))
        self.filename = ctk.StringVar()
        self.entry = ctk.CTkEntry(self.frame, textvariable=self.filename, height=22, font=fonts["filename"],
                                   fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
                                   border_width=1, placeholder_text="auto")
        self.entry.grid(row=1, column=2, sticky="ew", padx=4, pady=(0, 6))
        self.filename.trace_add("write", self._edit)
        self.entry.bind("<FocusOut>", lambda event: view.request_render(), add="+")
        self.badge = ctk.CTkButton(self.frame, text="", font=fonts["mode"], corner_radius=10,
                                     width=112, height=24, command=self._mode_menu)
        self.badge.grid(row=0, column=3, rowspan=2, padx=6)
        self.remove = ctk.CTkButton(self.frame, text="×", width=28, height=28, font=fonts["remove"],
                                      fg_color="transparent", hover_color=p["surface2"], text_color=p["muted"],
                                      command=self._remove)
        self.remove.grid(row=0, column=4, rowspan=2, padx=(0, 6))
        self.handle.bind("<ButtonPress-1>", self._drag_start)
        self.handle.bind("<B1-Motion>", self._drag)
        self.handle.bind("<ButtonRelease-1>", self._drag_end)
        self.window = view.canvas.create_window(4, 0, window=self.frame, anchor="nw", state="hidden")

    def focused(self):
        try:
            widget = self.view.gui.root.focus_get()
            while widget is not None:
                if widget is self.frame:
                    return True
                widget = getattr(widget, "master", None)
        except (tk.TclError, KeyError):
            pass
        return False

    def _edit(self, *_):
        if not self.changing:
            item = self.view.model.get(self.identity)
            if item is not None:
                item["filename"] = self.filename.get().strip()

    def _mode_menu(self):
        item = self.view.model.get(self.identity)
        if item is not None:
            self.view.gui._show_queue_mode_menu(item, self.badge)

    def _remove(self):
        self.view.remove_ids({self.identity})

    def _drag_start(self, event):
        self.view.drag_identity = self.identity

    def _drag(self, event):
        identity = self.view.drag_identity
        i = self.view.model.positions.get(identity)
        if i is None:
            return
        canvas = self.view.canvas
        y = event.y_root - canvas.winfo_rooty()
        if y < 0 or y > canvas.winfo_height():
            canvas.yview_scroll(-1 if y < 0 else 1, "units")
        j = int(canvas.canvasy(max(0, min(y, canvas.winfo_height() - 1))) // self.view.row_pixels())
        j = max(0, min(j, len(self.view.model.items) - 1))
        if i != j:
            self.view.swap(i, j)

    def _drag_end(self, event):
        self.view.drag_identity = None
        self.view.request_render()

    def bind_item(self, item):
        identity = item["_queue_id"]
        rebound = identity != self.identity
        self.identity = identity
        p = self.view.gui._p()
        state = self.view.model.states.get(identity, "idle")
        failed = state == "error"
        signature = (identity, item["url"], item.get("filename"), item.get("mode"), state,
                     getattr(self.view.gui, "_language", "en"), tuple(p.items()))
        if signature == self.snapshot:
            return
        self.snapshot = signature
        self.frame.configure(fg_color=p["surface"], border_color="#ef4444" if failed else p["border"])
        self.handle.configure(text_color=p["muted2"])
        self.url.configure(text=item["url"], text_color="#fca5a5" if failed else p["muted"])
        name = str(item.get("filename") or "")
        if rebound or (not self.focused() and self.filename.get() != name):
            self.changing = True
            try:
                self.filename.set(name)
            finally:
                self.changing = False
        self.entry.configure(fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"])
        mode = item.get("mode") or "auto"
        bg, fg = self.view.gui._badge_colors(mode)
        self.badge.configure(text=self.view.mode_label(mode, getattr(self.view.gui, "_language", "en")),
                             fg_color=bg, hover_color=bg, text_color=fg)
        self.remove.configure(hover_color=p["surface2"], text_color=p["muted"])
        colors = {"idle": p["muted2"], "running": "#3b82f6", "done": "#22c55e",
                  "error": "#ef4444", "skip": "#f59e0b"}
        self.dot.configure(bg=p["surface"])
        self.dot.delete("all")
        self.dot.create_oval(2, 2, 8, 8, fill=colors.get(state, p["muted2"]), outline="")

    def legacy_tuple(self):
        return self.frame, self.dot, self.url, self.badge, self.remove


class VirtualQueue:
    def __init__(self, gui, mode_label):
        import customtkinter as ctk

        self.gui, self.mode_label = gui, mode_label
        self.model = QueueIndex(gui._queue_data)
        self.canvas = gui._qlist._parent_canvas
        self.rows = []
        self.drag_identity = None
        self.closed = False
        self.after_id = None
        self._scroll_fractions = None
        self.fonts = gui._queue_row_fonts = {
            "handle": ctk.CTkFont("Segoe UI", 14), "url": ctk.CTkFont("Consolas", 9),
            "filename": ctk.CTkFont("Segoe UI", 10), "mode": ctk.CTkFont("Segoe UI", 9, "bold"),
            "remove": ctk.CTkFont("Segoe UI", 13),
        }
        # Keep CTk's scrollbar/wheel integration, but render directly in the
        # viewport canvas instead of creating one enormous native inner frame.
        gui._qlist.unbind("<Configure>")
        self.canvas.itemconfigure(gui._qlist._create_window_id, state="hidden")
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.canvas.bind("<Configure>", lambda event: self.changed(), add="+")
        self.canvas.bind("<Destroy>", self._destroyed, add="+")
        self.changed()

    def row_pixels(self):
        return self.pixels(72)

    def pixels(self, value):
        return max(1, round(self.gui._qlist._apply_widget_scaling(value)))

    def _scrolled(self, first, last):
        self.gui._qlist._scrollbar.set(first, last)
        bounds = (first, last)
        if bounds != self._scroll_fractions:
            self._scroll_fractions = bounds
            self.request_render()

    def _destroyed(self, event):
        if event.widget is self.canvas:
            self.close()

    def close(self):
        self.closed = True
        if self.after_id is not None:
            try:
                self.canvas.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def changed(self):
        if self.closed:
            return
        self.model.appended()
        self.canvas.configure(scrollregion=(0, 0, max(1, self.canvas.winfo_width()),
                                            max(1, len(self.model.items) * self.row_pixels())))
        self.gui._update_queue_count()
        self.request_render()

    def request_render(self):
        if not self.closed and self.after_id is None:
            self.after_id = self.canvas.after(8, self.render)

    def render(self):
        self.after_id = None
        if self.closed:
            return
        height = self.row_pixels()
        count = len(self.model.items)
        start = max(0, min(count, int(self.canvas.canvasy(0) // height)) - 2)
        slots = min(64, math.ceil(max(1, self.canvas.winfo_height()) / height) + 5)
        desired = self.model.items[start:min(count, start + slots)]
        wanted = {item["_queue_id"] for item in desired}
        assigned = {}
        free = []
        for row in self.rows:
            if row.identity in wanted or self.model.get(row.identity) is not None and (row.focused() or row.identity == self.drag_identity):
                assigned[row.identity] = row
            else:
                free.append(row)
        deadline = time.monotonic() + .006
        for item in desired:
            identity = item["_queue_id"]
            if identity not in assigned:
                if free:
                    row = free.pop()
                else:
                    row = VirtualRow(self)
                    self.rows.append(row)
                assigned[identity] = row
                row.bind_item(item)
                if time.monotonic() > deadline:
                    self.request_render()
                    break
        for identity, row in assigned.items():
            item = self.model.get(identity)
            if item is None:
                continue
            row.bind_item(item)
            self.canvas.itemconfigure(row.window, state="normal", width=max(1, self.canvas.winfo_width() - 2 * self.pixels(4)), height=self.pixels(66))
            self.canvas.coords(row.window, self.pixels(4), self.model.positions[identity] * height + self.pixels(3))
        for row in free:
            self.canvas.itemconfigure(row.window, state="hidden")
            row.identity = None
        self.gui._queue_rows = [row.legacy_tuple() for row in self.rows]

    def set_state(self, identity, state):
        if not self.closed and identity in self.model.positions:
            self.model.states[identity] = state
            self.request_render()

    def swap(self, i, j):
        self.model.swap(i, j)
        self.request_render()

    def remove_ids(self, identities):
        for row in self.rows:
            if row.identity in identities and row.focused():
                self.canvas.focus_set()
        removed = self.model.remove_ids(identities)
        self.changed()
        return removed

    def remove_at(self, index):
        if 0 <= index < len(self.model.items):
            self.remove_ids({self.model.items[index]["_queue_id"]})

    def clear(self):
        self.canvas.focus_set()
        self.drag_identity = None
        self.model.clear()
        self.changed()

    def reveal(self, identities):
        if self.closed:
            return
        matches = [self.model.positions[key] for key in identities if key in self.model.positions]
        if matches:
            for key in identities:
                self.set_state(key, "error")
            total = max(1, len(self.model.items) * self.row_pixels())
            self.canvas.yview_moveto(min(matches) * self.row_pixels() / total)
            self.request_render()
