from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CYOA_GUI_SMOKE") != "1",
    reason="set CYOA_GUI_SMOKE=1 to run the live CustomTkinter layout test",
)


def _visible_texts(widget):
    values = []
    for child in widget.winfo_children():
        try:
            if child.winfo_ismapped():
                text = child.cget("text")
                if text:
                    values.append(str(text))
        except Exception:
            pass
        values.extend(_visible_texts(child))
    return values


def test_live_gui_settings_location_and_expanded_progress_geometry():
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    root = ctk.CTk()
    root.geometry("1600x1000+20+20")
    gui = None
    try:
        gui = CYOADownloaderGUI(root)
        root.update_idletasks()
        root.update()
        assert root.title() == "CYOA Downloader v1.0.5"

        input_panel, queue_panel = gui._dispatch_gui_patch("_v462_find_main_panels")
        gui._v46_apply_progress_visibility(True)
        root.update_idletasks()
        root.update()

        assert input_panel.grid_info()
        assert queue_panel.grid_info()
        assert gui._v463_progress_details.winfo_ismapped()
        assert not gui._v463_progress_compact.winfo_ismapped()
        assert int(gui._qlist._parent_canvas.cget("height")) == 64
        root_bottom = root.winfo_rooty() + root.winfo_height()
        controls_bottom = (
            gui._v46_cancel_btn.winfo_rooty() + gui._v46_cancel_btn.winfo_height()
        )
        assert controls_bottom <= root_bottom

        gui._v46_apply_progress_visibility(False)
        root.update_idletasks()
        assert input_panel.grid_info()
        assert queue_panel.grid_info()
        assert not gui._v463_progress_details.winfo_ismapped()
        assert gui._v463_progress_compact.winfo_ismapped()
        assert int(gui._qlist._parent_canvas.cget("height")) >= 90

        gui._settings_maintenance_panel()
        root.update_idletasks()
        settings_window = gui._singleton_windows["settings_maintenance"]
        settings_text = "\n".join(_visible_texts(settings_window))
        assert "JavaScript Archive Policy" in settings_text or "Kebijakan Arsip JavaScript" in settings_text
        assert "Open Guide" in settings_text or "Buka Panduan" in settings_text
        settings_window.destroy()
        root.update_idletasks()

        gui._toggles_panel()
        root.update_idletasks()
        features_window = gui._singleton_windows["feature_toggles"]
        features_text = "\n".join(_visible_texts(features_window))
        assert "JavaScript website archive" not in features_text
        assert "Arsip website JavaScript" not in features_text
    finally:
        if gui is not None:
            for window in list(getattr(gui, "_singleton_windows", {}).values()):
                try:
                    window.destroy()
                except Exception:
                    pass
        root.destroy()
