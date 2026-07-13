"""Small GUI widget/scheduling helpers.

These low-risk helper bodies are used by the composed GUI behavior and are
intentionally behavior-preserving.
"""

from __future__ import annotations

import os
from typing import Any, List

from ..logging_setup import logger
from .logging_ui import GUILogHandler


def _gui_exists(widget: Any) -> bool:
    try:
        return widget is not None and bool(widget.winfo_exists())
    except Exception:
        return False


def _v25_safe_after(win: Any, fn) -> None:
    """Run a Tk update only while the target window still exists."""
    try:
        if win is not None and bool(win.winfo_exists()):
            win.after(0, fn)
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v25_safe_after (line 20307): %s", _ignored_exc)

def _v25_safe_after_widget(root: Any, widget: Any, fn, delay: int = 0) -> None:
    """Schedule ``fn`` on the Tk loop, guarding BOTH schedule- and run-time.

    ``_v25_safe_after`` only checks existence at schedule
    time; a worker thread can pass that check and then the window/widget is
    destroyed before the queued callback runs on the main loop, so a callback
    that calls ``widget.configure()`` / ``.attributes()`` / CTk ``.set()`` hits
    a dead widget and raises TclError. This variant re-checks the concrete
    target widget at run time inside the queued closure, closing that TOCTOU
    gap. Tk *variables* (StringVar/DoubleVar.set) survive widget destruction,
    so they don't need this; this is for live-widget mutations.
    """
    try:
        if root is None or not bool(root.winfo_exists()):
            return
        def _runner():
            try:
                if _gui_exists(widget):
                    fn()
            except Exception as _exc:
                logger.debug("safe_after_widget callback skipped: %s", _exc)
        root.after(delay, _runner)
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v25_safe_after_widget: %s", _ignored_exc)

def _v25_center_window(win: Any, root: Any, width: int, height: int, *, min_w: int = 720, min_h: int = 520) -> None:
    try:
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w = min(width, max(min_w, sw - 160))
        h = min(height, max(min_h, sh - 140))
        x = max(24, (sw - w) // 2)
        y = max(24, (sh - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.minsize(min_w, min_h)
    except Exception:
        try:
            win.geometry(f"{width}x{height}")
            win.minsize(min_w, min_h)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _v25_center_window (line 20324): %s", _ignored_exc)

def _v27_ai_provider_values() -> List[str]:
    return ["anthropic", "openai", "gemini", "ollama", "deepseek", "qwen", "groq", "openrouter", "custom"]

def _v27_safe_after(win: Any, fn: Any) -> None:
    try:
        if _gui_exists(win):
            win.after(0, lambda: fn() if _gui_exists(win) else None)
    except Exception as exc:
        logger.debug(f"GUI callback skipped: {exc}")

def _v27_open_path(path: str) -> None:
    # Open a file/folder with the platform default handler.
    # Mirrors CYOADownloaderGUI._open_path_in_os; the previous call to an
    # undefined open_path() raised NameError, silently breaking this button.
    try:
        import subprocess, platform
        if not path or not os.path.exists(path):
            logger.warning(f"Open path failed: path not found: {path}")
            return
        sys_name = platform.system()
        if sys_name == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys_name == "Darwin":
            subprocess.Popen(["open", path], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", path], close_fds=True)
    except Exception as exc:
        logger.warning(f"Open path failed: {path}: {exc}")


__all__ = [
    "GUILogHandler", "_v25_safe_after", "_v25_safe_after_widget",
    "_v25_center_window", "_v27_safe_after", "_v27_open_path",
    "_v27_ai_provider_values",
]
