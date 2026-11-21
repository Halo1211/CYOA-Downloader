"""Bounded card pages, built one at a time to leave Tk input time."""

from __future__ import annotations

import customtkinter as ctk


class PagedCards(ctk.CTkFrame):
    PAGE_SIZE = 12

    def __init__(self, parent, palette, build, *, is_en=True):
        super().__init__(parent, fg_color="transparent")
        self.build = build
        self.items = []
        self.page = 0
        self.visible = []
        self.ready = True
        self.closed = False
        self.after_id = None
        self.empty_text = "No entries." if is_en else "Tidak ada entri."
        self.body = ctk.CTkScrollableFrame(self, fg_color=palette["surface"], corner_radius=8)
        self.body.pack(fill="both", expand=True)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=(6, 0))
        self.previous = ctk.CTkButton(bar, text="←", width=50, command=lambda: self.show(self.page - 1))
        self.previous.pack(side="left")
        self.label = ctk.CTkLabel(bar, text="0 / 0", text_color=palette["muted"])
        self.label.pack(side="left", expand=True)
        self.next = ctk.CTkButton(bar, text="→", width=50, command=lambda: self.show(self.page + 1))
        self.next.pack(side="right")
    def destroy(self):
        self.closed = True
        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None
        super().destroy()

    def set_items(self, items):
        self.items = list(items)
        self.show(0)

    def show(self, page):
        if self.closed:
            return
        if self.after_id is not None:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.page = max(0, min(page, (len(self.items) - 1) // self.PAGE_SIZE))
        for child in self.body.winfo_children():
            child.destroy()
        self.visible.clear()
        self.body._parent_canvas.yview_moveto(0)
        start = self.page * self.PAGE_SIZE
        stop = min(len(self.items), start + self.PAGE_SIZE)
        self.label.configure(text=f"{start + 1 if self.items else 0}–{stop} / {len(self.items)}")
        self.previous.configure(state="normal" if self.page else "disabled")
        self.next.configure(state="normal" if stop < len(self.items) else "disabled")
        pending = iter(self.items[start:stop])
        self.ready = False

        def step():
            self.after_id = None
            try:
                item = next(pending)
            except StopIteration:
                if not self.items:
                    ctk.CTkLabel(self.body, text=self.empty_text).pack(pady=20)
                self.ready = True
                return
            card = self.build(self.body, item)
            card.pack(fill="x", padx=5, pady=4)
            self.visible.append(card)
            self.after_id = self.after(8, step)

        self.after_id = self.after(1, step)
