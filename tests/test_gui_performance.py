from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CYOA_GUI_SMOKE") != "1",
    reason="set CYOA_GUI_SMOKE=1 to run the live CustomTkinter performance test",
)


def test_idle_pollers_back_off_for_low_spec_computers(monkeypatch) -> None:
    """The always-on main-window pollers must not monopolize Tk's event loop."""
    import customtkinter as ctk

    from cyoa_downloader_app.gui import app as gui_app
    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    real_load = gui_app._load_settings
    monkeypatch.setattr(
        gui_app,
        "_load_settings",
        lambda: {**real_load(), "language": "en"},
    )
    traversals = []
    original_translate_tree = CYOADownloaderGUI._translate_widget_tree

    def recording_translate_tree(self, widget):
        traversals.append(widget)
        return original_translate_tree(self, widget)

    monkeypatch.setattr(
        CYOADownloaderGUI, "_translate_widget_tree", recording_translate_tree
    )

    root = ctk.CTk()
    scheduled: list[tuple[int, str]] = []
    original_after = root.after

    def recording_after(delay, callback=None, *args):
        callback_name = getattr(callback, "__name__", "") if callback else ""
        scheduled.append((int(delay), callback_name))
        return original_after(delay, callback, *args)

    monkeypatch.setattr(root, "after", recording_after)
    gui = None
    try:
        gui = CYOADownloaderGUI(root)
        root.update_idletasks()
        recurring = {
            name: delay
            for delay, name in scheduled
            if name in {"_v465_poll_log", "_v46_poll_progress"}
        }
        assert recurring.get("_v465_poll_log", 0) >= 300, scheduled
        assert recurring.get("_v46_poll_progress", 0) >= 300, scheduled
        assert traversals == []
    finally:
        if gui is not None:
            gui._v46_finish_close()
        elif root.winfo_exists():
            root.destroy()
