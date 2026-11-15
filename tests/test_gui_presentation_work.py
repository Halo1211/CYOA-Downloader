"""Exercise startup allocation and the actual sidebar mouse bindings."""

import os
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CYOA_GUI_SMOKE") != "1", reason="requires real Tk; set CYOA_GUI_SMOKE=1")


@pytest.fixture(scope="module")
def live_gui(tmp_path_factory):
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("USERPROFILE", str(tmp_path_factory.mktemp("presentation-profile")))
        bars = []
        original = ctk.CTkProgressBar.__init__

        def construct(widget, *args, **options):
            original(widget, *args, **options)
            bars.append(widget)

        patch.setattr(ctk.CTkProgressBar, "__init__", construct)
        root = ctk.CTk()
        root.attributes("-alpha", 0)
        gui = CYOADownloaderGUI(root)
        root.geometry("1280x900")
        root.update()
        try:
            yield root, gui, bars
        finally:
            gui._v46_finish_close()


def test_startup_never_constructs_discarded_progress_bars(live_gui):
    _root, gui, bars = live_gui
    assert all(bar.winfo_exists() for bar in bars), "startup constructed then destroyed progress widgets"
    assert set(bars) == {gui._pb, gui._v46_overall_pb, gui._v46_job_pb, gui._v46_file_pb}


def test_startup_panel_discovery_points_to_input_and_queue(live_gui):
    _root, gui, _bars = live_gui
    input_panel, queue_panel = gui._dispatch_gui_patch("_v462_find_main_panels")
    assert input_panel is gui._input_row.master
    assert queue_panel is gui._qlist._parent_frame.master


def test_idle_mouse_crossings_do_not_reselect_mode_or_read_settings(live_gui, monkeypatch):
    from cyoa_downloader_app.gui import app

    _root, gui, _bars = live_gui
    gui._select_mode("auto")
    original_info = gui._update_mode_info
    settings = Mock(wraps=app._load_settings)
    information = Mock(wraps=original_info)
    unrelated = gui._mode_btns["zip"][0]
    repaint = Mock(wraps=unrelated.configure)
    monkeypatch.setattr(app, "_load_settings", settings)
    monkeypatch.setattr(gui, "_update_mode_info", information)
    monkeypatch.setattr(unrelated, "configure", repaint)
    row, _bar, icon, name, description = gui._mode_btns["embed"]
    for widget in (row, icon, name.master, name, description):
        widget._canvas.event_generate("<Enter>")
        assert row.cget("fg_color") == gui._p()["surface"]
        widget._canvas.event_generate("<Leave>")
        assert row.cget("fg_color") == "transparent"
    assert gui._mode_var == "auto"
    information.assert_not_called()
    settings.assert_not_called()
    repaint.assert_not_called()

    # A real selection still refreshes its description from current settings.
    row._canvas.event_generate("<Button-1>")
    assert gui._mode_var == "embed"
    information.assert_called_once_with("embed")
    settings.assert_called_once()
    row._canvas.event_generate("<Enter>")
    row._canvas.event_generate("<Leave>")
    assert row.cget("fg_color") == gui._p()["sel_row"]
