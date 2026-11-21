"""Parse lists off-thread and commit queue rows in short Tk turns."""

from __future__ import annotations

import threading
import time
import uuid
from tkinter import TclError, messagebox
from typing import Any

from ..core.progress import DownloadCancelledError
from ..logging_setup import logger

_WIDGET_ERRORS = (AttributeError, RuntimeError, TclError, TypeError, ValueError)
# Optional CSV/spreadsheet parsers expose backend-specific exception classes.
# Report their failures on the GUI thread and always release the busy state.
_PARSER_ERRORS = (Exception,)


class QueueImport:
    def __init__(self, gui: Any, path: str) -> None:
        self.gui = gui
        self.path = path
        self.default_mode = gui._mode_var
        self.cancelled = threading.Event()
        self.after_id = None
        self.items = iter(())
        self.total = self.added = 0
        gui._queue_import_task = self
        gui._queue_import_busy = True
        self._controls(True)
        is_id = getattr(gui, "_language", "en") == "id"
        gui._set_status("Membaca daftar antrean…" if is_id else "Reading queue list…")

    def _controls(self, busy: bool) -> None:
        for name in ("_import_button", "_export_button", "_add_btn", "_dl_btn"):
            button = getattr(self.gui, name, None)
            if button is not None:
                state = "disabled" if busy or (name == "_dl_btn" and self.gui._is_running) else "normal"
                try:
                    button.configure(state=state)
                except _WIDGET_ERRORS:
                    pass  # The window may already be closing.

    def _active(self) -> bool:
        return not self.cancelled.is_set() and getattr(self.gui, "_queue_import_task", None) is self

    def start(self, reader) -> None:
        def parse() -> None:
            try:
                items = reader(self.path)
            except DownloadCancelledError:
                if not self.cancelled.is_set():
                    self.gui._run_on_ui_thread(self.cancel)
                return
            except _PARSER_ERRORS as exc:
                error = str(exc)
                if not self.cancelled.is_set():
                    self.gui._run_on_ui_thread(lambda error=error: self.failed(error))
                return
            if not self.cancelled.is_set():
                self.gui._run_on_ui_thread(lambda: self.accept(items))

        self.thread = threading.Thread(target=parse, daemon=True, name="cyoa-queue-import")
        self.thread.start()

    def accept(self, items) -> None:
        if not self._active():
            return
        if not items:
            self.finish()
            messagebox.showwarning("Import Failed", "No valid URLs found.")
            return
        self.total = len(items)
        self.items = iter(items)
        self.drain()

    def drain(self) -> None:
        self.after_id = None
        if not self._active():
            return
        deadline = time.monotonic() + 0.006
        queue_view = getattr(self.gui, "_queue_view", None)
        try:
            for _ in range(256 if queue_view is not None else 4):
                item = next(self.items)
                mode = str(item.get("mode") or "").strip() or self.default_mode
                row = {
                    "url": item["url"],
                    "filename": item.get("filename", ""),
                    "mode": mode,
                    "_queue_id": uuid.uuid4().hex,
                }
                count = len(self.gui._queue_rows)
                self.gui._queue_data.append(row)
                try:
                    if queue_view is None:
                        self.gui._make_queue_row(row["url"], mode, row["filename"])
                except _WIDGET_ERRORS:
                    if len(self.gui._queue_rows) == count:
                        self.gui._queue_data.pop()
                    raise
                self.added += 1
                if self.added == self.total:
                    if queue_view is not None:
                        queue_view.changed()
                    self.finish()
                    is_id = getattr(self.gui, "_language", "en") == "id"
                    self.gui._set_status(
                        f"Antrean diimpor: {self.added} item." if is_id else f"Imported {self.added} item(s)."
                    )
                    logger.info("Imported %s item(s) from %s", self.added, self.path)
                    return
                if time.monotonic() >= deadline:
                    break
            if queue_view is not None:
                queue_view.changed()
            is_id = getattr(self.gui, "_language", "en") == "id"
            self.gui._set_status(f"{'Mengimpor antrean' if is_id else 'Importing queue'}… {self.added}/{self.total}")
            self.after_id = self.gui.root.after(8, self.drain)
        except DownloadCancelledError:
            self.cancel()
        except (StopIteration, *_WIDGET_ERRORS) as exc:
            self.failed(str(exc))

    def failed(self, error: str) -> None:
        if not self._active():
            return
        self.finish()
        logger.error("Queue import failed: %s", error)
        self.gui._set_status("Queue import failed; see log.")

    def finish(self) -> None:
        if getattr(self.gui, "_queue_import_task", None) is not self:
            return
        self.cancelled.set()
        if self.after_id is not None:
            try:
                self.gui.root.after_cancel(self.after_id)
            except _WIDGET_ERRORS:
                pass
        self.after_id = None
        self.items = iter(())
        self.gui._queue_import_task = None
        self.gui._queue_import_busy = False
        self._controls(False)

    def cancel(self) -> None:
        if self._active():
            self.finish()
            is_id = getattr(self.gui, "_language", "en") == "id"
            self.gui._set_status("Impor antrean dibatalkan." if is_id else "Queue import cancelled.")


def begin_queue_import(gui: Any, path: str, reader) -> None:
    if not getattr(gui, "_queue_import_busy", False):
        task = QueueImport(gui, path)
        try:
            task.start(reader)
        except (OSError, RuntimeError) as exc:
            task.failed(str(exc))


def cancel_queue_import(gui: Any) -> None:
    task = getattr(gui, "_queue_import_task", None)
    if task is not None:
        task.cancel()
