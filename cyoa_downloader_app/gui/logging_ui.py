"""GUI log queue handler and render helpers.

Phase 43 extracts the v46.5 GUI logging surface from ``legacy.py``. The class
and functions are assigned back to ``CYOADownloaderGUI`` by the historical patch
stack, preserving handler markers, queue behavior, tag names, and truncation.
"""

from __future__ import annotations

import logging
import queue as log_queue_module
import re
import threading
from datetime import datetime
from typing import Any, List

from ..logging_setup import logger, _formatter

class GUILogHandler(logging.Handler):
    """Non-blocking, level-aware bridge from worker logs to the Tk main thread."""

    def __init__(self, q: log_queue_module.Queue) -> None:
        super().__init__()
        self.q = q
        self._dropped = 0
        self._drop_lock = threading.Lock()
        self._cyoa_gui_handler = True

    def _take_dropped(self) -> int:
        with self._drop_lock:
            value = self._dropped
            self._dropped = 0
            return value

    def _restore_dropped(self, value: int) -> None:
        if value <= 0:
            return
        with self._drop_lock:
            self._dropped += value

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record)
            dropped = self._take_dropped()
            if dropped:
                notice = (
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]}"
                    f" - WARNING - GUI log queue was saturated; "
                    f"{dropped} older line(s) were skipped."
                )
                try:
                    self.q.put_nowait(notice)
                except log_queue_module.Full:
                    self._restore_dropped(dropped)
            try:
                self.q.put_nowait(rendered)
            except log_queue_module.Full:
                # Retain the newest activity: evict one stale line instead of
                # blocking the worker or silently discarding the latest error.
                evicted = 0
                try:
                    self.q.get_nowait()
                    evicted = 1
                except log_queue_module.Empty as empty_exc:
                    logger.debug(f"GUI log queue reported full but was empty: {empty_exc}")
                try:
                    self.q.put_nowait(rendered)
                except log_queue_module.Full:
                    evicted += 1
                self._restore_dropped(evicted)
        except Exception:
            self.handleError(record)

def _v465_configure_log_tags(self: Any) -> None:
    if not hasattr(self, "_log_txt"):
        return
    dark = bool(getattr(self, "_is_dark", True))
    palette = {
        "TIMESTAMP": "#64748b",
        "SEPARATOR": "#334155" if dark else "#94a3b8",
        "DEBUG": "#64748b",
        "INFO": "#cbd5e1" if dark else "#334155",
        "DOWNLOAD": "#38bdf8" if dark else "#0369a1",
        "NETWORK": "#22d3ee" if dark else "#0e7490",
        "AUTO": "#c084fc" if dark else "#7e22ce",
        "DISCOVERY": "#a78bfa" if dark else "#6d28d9",
        "SETTINGS": "#2dd4bf" if dark else "#0f766e",
        "RETRY": "#fb923c" if dark else "#c2410c",
        "PACKAGE": "#f472b6" if dark else "#be185d",
        "SKIPPED": "#94a3b8" if dark else "#64748b",
        "SUCCESS": "#4ade80" if dark else "#15803d",
        "WARNING": "#fbbf24" if dark else "#b45309",
        "ERROR": "#fb7185" if dark else "#dc2626",
        "CRITICAL": "#f43f5e" if dark else "#b91c1c",
        "LEVEL_DEBUG": "#64748b",
        "LEVEL_INFO": "#38bdf8" if dark else "#0369a1",
        "LEVEL_WARNING": "#fbbf24" if dark else "#b45309",
        "LEVEL_ERROR": "#fb7185" if dark else "#dc2626",
        "LEVEL_CRITICAL": "#f43f5e" if dark else "#b91c1c",
    }
    for tag, color in palette.items():
        self._log_txt.tag_configure(tag, foreground=color)
    self._log_txt.tag_configure("CRITICAL", underline=True)
    for tag in ("LEVEL_DEBUG", "LEVEL_INFO", "LEVEL_WARNING", "LEVEL_ERROR", "LEVEL_CRITICAL"):
        self._log_txt.tag_configure(tag, font=("Consolas", 10, "bold"))

def _v465_log_tag(level_no: int, level_name: str, message: str) -> str:
    upper = level_name.upper()
    lower = message.lower()
    if level_no >= logging.CRITICAL or upper == "CRITICAL":
        return "CRITICAL"
    if level_no >= logging.ERROR or upper == "ERROR":
        return "ERROR"
    if level_no >= logging.WARNING or upper in {"WARNING", "WARN"}:
        return "WARNING"
    if level_no <= logging.DEBUG or upper == "DEBUG":
        return "DEBUG"
    if any(token in lower for token in ("retry", "retrying", "mengulang", "percobaan ulang")):
        return "RETRY"
    if "[auto-detect]" in lower or "auto-detect" in lower:
        return "AUTO"
    if any(token in lower for token in ("successful", "completed", "complete", "done", "resolved viewer")):
        return "SUCCESS"
    if any(token in lower for token in ("[settings]", "setting saved", "policy saved", "settings.json")):
        return "SETTINGS"
    if any(token in lower for token in ("skipped", "dilewati", "ignored")):
        return "SKIPPED"
    if any(token in lower for token in ("http/2", "proxy", "dns", "cloudflare", "connection", "timeout")):
        return "NETWORK"
    if any(token in lower for token in ("discover", "scanning", "scan ", "crawler", "route found", "manifest")):
        return "DISCOVERY"
    if any(token in lower for token in ("packaging", "zip created", "archive created", "creating zip")):
        return "PACKAGE"
    if any(token in lower for token in ("downloaded:", "downloading", "asset:", "asset ", "saved:", "fetching")):
        return "DOWNLOAD"
    return "INFO"

def _v465_setup_logging(self: Any) -> None:
    # Remove handlers from previous GUI instances/reloads by marker, not class
    # identity, because this module intentionally supports compatibility layers.
    for handler in logger.handlers[:]:
        if getattr(handler, "_cyoa_gui_handler", False) or handler.__class__.__name__ == "GUILogHandler":
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception as exc:
                logger.debug(f"GUI log handler close failed: {exc}")
    handler = GUILogHandler(self._log_queue)
    handler.setFormatter(_formatter)
    logger.addHandler(handler)
    self._v465_log_poll_after_id = None
    _v465_configure_log_tags(self)

def _v465_insert_log_line(self: Any, item: Any) -> None:
    if isinstance(item, tuple) and len(item) == 3:
        level_no, level_name, rendered = item
        try:
            level_no = int(level_no)
        except (TypeError, ValueError):
            level_no = logging.INFO
        level_name = str(level_name or "INFO")
        rendered = str(rendered)
    else:
        rendered = str(item)
        if " - CRITICAL - " in rendered:
            level_no, level_name = logging.CRITICAL, "CRITICAL"
        elif " - ERROR - " in rendered:
            level_no, level_name = logging.ERROR, "ERROR"
        elif " - WARNING - " in rendered:
            level_no, level_name = logging.WARNING, "WARNING"
        elif " - DEBUG - " in rendered:
            level_no, level_name = logging.DEBUG, "DEBUG"
        else:
            level_no, level_name = logging.INFO, "INFO"
    semantic = _v465_log_tag(level_no, level_name, rendered)
    match = re.match(r"^(.*?) - ([A-Z]+) - (.*)$", rendered, re.DOTALL)
    if match:
        shown_level = match.group(2)
        level_tag = f"LEVEL_{shown_level}" if shown_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "LEVEL_INFO"
        self._log_txt.insert("end", match.group(1), "TIMESTAMP")
        self._log_txt.insert("end", "  ", "SEPARATOR")
        self._log_txt.insert("end", f"{shown_level:<8}", level_tag)
        self._log_txt.insert("end", "  ", "SEPARATOR")
        self._log_txt.insert("end", match.group(3) + "\n", semantic)
    else:
        self._log_txt.insert("end", rendered + "\n", semantic)

def _v465_poll_log(self: Any) -> None:
    self._v465_log_poll_after_id = None
    try:
        if not self.root.winfo_exists() or not self._log_txt.winfo_exists():
            return
    except Exception:
        return
    batch: List[Any] = []
    try:
        for _ in range(500):
            batch.append(self._log_queue.get_nowait())
    except log_queue_module.Empty as empty_exc:
        _ = empty_exc  # expected non-blocking queue control flow
    if batch:
        self._log_txt.configure(state="normal")
        try:
            for item in batch:
                try:
                    _v465_insert_log_line(self, item)
                except Exception as exc:
                    self._log_txt.insert("end", f"GUI log render error: {exc}\n", "ERROR")
            try:
                line_count = int(self._log_txt.index("end-1c").split(".")[0])
                if line_count > 5000:
                    self._log_txt.delete("1.0", f"{line_count - 4500}.0")
            except Exception as exc:
                logger.debug(f"GUI log trimming failed: {exc}")
            self._log_txt.see("end")
        finally:
            self._log_txt.configure(state="disabled")
    try:
        if self.root.winfo_exists():
            self._v465_log_poll_after_id = self.root.after(100, self._poll_log)
    except Exception as exc:
        logger.debug(f"GUI log polling could not be rescheduled: {exc}")

def _v465_safe_message(self: Any, title: str, message: str) -> None:
    """Compatibility helper used by older dialogs; always execute on Tk thread."""
    from tkinter import messagebox

    def show() -> None:
        try:
            messagebox.showerror(str(title), str(message), parent=self.root)
        except Exception as exc:
            logger.error(f"{title}: {message} (dialog failed: {exc})")

    if threading.current_thread() is threading.main_thread():
        show()
    else:
        self.root.after(0, show)

__all__ = [
    "GUILogHandler", "_v465_configure_log_tags", "_v465_log_tag",
    "_v465_setup_logging", "_v465_insert_log_line", "_v465_poll_log",
    "_v465_safe_message",
]
