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
            if not child.winfo_ismapped():
                continue
            text = child.cget("text")
            if text:
                values.append(str(text))
        except Exception:
            pass
        values.extend(_visible_texts(child))
    return values


def _button_with_text(widget, expected):
    for child in widget.winfo_children():
        try:
            if expected in str(child.cget("text")) and callable(getattr(child, "invoke", None)):
                return child
        except Exception:
            pass
        match = _button_with_text(child, expected)
        if match is not None:
            return match
    return None


def _visible_widget_with_text(widget, expected):
    for child in widget.winfo_children():
        try:
            if not child.winfo_ismapped():
                continue
            if str(child.cget("text")) == expected:
                return child
        except Exception:
            pass
        match = _visible_widget_with_text(child, expected)
        if match is not None:
            return match
    return None


def _widget_with_exact_text(widget, expected):
    for child in widget.winfo_children():
        try:
            if str(child.cget("text")) == expected:
                return child
        except Exception:
            pass
        match = _widget_with_exact_text(child, expected)
        if match is not None:
            return match
    return None


def _buttons_containing_text(widget, expected):
    matches = []
    for child in widget.winfo_children():
        try:
            if child.__class__.__name__ == "CTkButton" and expected in str(child.cget("text")):
                matches.append(child)
        except Exception:
            pass
        matches.extend(_buttons_containing_text(child, expected))
    return matches


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
        assert root.title() == "CYOA Downloader v1.0.8"

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
        root.update()
        settings_window = gui._singleton_windows["settings_maintenance"]
        settings_text = "\n".join(_visible_texts(settings_window))
        assert "Download Features" in settings_text or "Fitur Download" in settings_text
        assert "JavaScript Archive Policy" in settings_text or "Kebijakan Arsip JavaScript" in settings_text
        assert "Open Guide" in settings_text or "Buka Panduan" in settings_text
        guide_buttons = (
            _buttons_containing_text(settings_window, "Open Guide")
            + _buttons_containing_text(settings_window, "Buka Panduan")
        )
        assert len(guide_buttons) == 1

        feature_title = (
            _widget_with_exact_text(settings_window, "Download Features")
            or _widget_with_exact_text(settings_window, "Fitur Download")
        )
        credentials_title = (
            _widget_with_exact_text(settings_window, "Access & Credentials")
            or _widget_with_exact_text(settings_window, "Akses & Kredensial")
        )
        archive_title = (
            _widget_with_exact_text(settings_window, "JavaScript Archive Policy")
            or _widget_with_exact_text(settings_window, "Kebijakan Arsip JavaScript")
        )
        assert feature_title is not None
        assert credentials_title is not None
        assert archive_title is not None
        assert int(feature_title.grid_info()["row"]) < int(credentials_title.grid_info()["row"])
        assert int(credentials_title.grid_info()["row"]) < int(archive_title.grid_info()["row"])

        integrations_button = (
            _button_with_text(settings_window, "Integrations")
            or _button_with_text(settings_window, "Integrasi")
        )
        assert integrations_button is not None
        integrations_button.invoke()
        root.update_idletasks()
        root.update()
        integrations_text = "\n".join(_visible_texts(settings_window))
        assert "AI Assist" in integrations_text
        assert "Cloudflare / FlareSolverr" in integrations_text

        provider_label = _visible_widget_with_text(settings_window, "Provider")
        assert provider_label is not None
        ai_card = provider_label.master
        while ai_card is not settings_window and not any(
            isinstance(child, ctk.CTkOptionMenu) for child in ai_card.winfo_children()
        ):
            ai_card = ai_card.master
        provider_menu = next(
            child for child in ai_card.winfo_children()
            if isinstance(child, ctk.CTkOptionMenu)
        )
        model_entry = next(
            child for child in ai_card.winfo_children()
            if isinstance(child, ctk.CTkEntry)
        )
        target_provider = "openai" if provider_menu.get() != "openai" else "anthropic"
        provider_menu.set(target_provider)
        root.update_idletasks()
        root.update()
        from cyoa_downloader_app.gui import app as gui_app
        assert model_entry.get() in gui_app._ai_model_options(target_provider)

        maintenance_button = (
            _button_with_text(settings_window, "Maintenance")
            or _button_with_text(settings_window, "Pemeliharaan")
        )
        assert maintenance_button is not None
        maintenance_button.invoke()
        root.update_idletasks()
        root.update()
        maintenance_text = "\n".join(_visible_texts(settings_window))
        assert "Image cache" in maintenance_text or "Cache gambar" in maintenance_text
        assert "Offline viewers" in maintenance_text or "Viewer offline" in maintenance_text
        settings_window.destroy()
        root.update_idletasks()

        gui._toggles_panel()
        root.update_idletasks()
        root.update()
        settings_window = gui._singleton_windows["settings_maintenance"]
        settings_text = "\n".join(_visible_texts(settings_window))
        assert settings_window.winfo_exists()
        assert "JavaScript website archive" not in settings_text
        assert "Arsip website JavaScript" not in settings_text
        assert "feature_toggles" not in gui._singleton_windows
    finally:
        if gui is not None:
            for window in list(getattr(gui, "_singleton_windows", {}).values()):
                try:
                    window.destroy()
                except Exception:
                    pass
        root.destroy()


def test_import_export_stay_visible_without_maximizing():
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    root = ctk.CTk()
    root.geometry("1100x720+20+20")
    gui = None
    try:
        gui = CYOADownloaderGUI(root)
        root.update_idletasks()
        root.update()

        assert gui._import_button.winfo_ismapped()
        assert gui._export_button.winfo_ismapped()
        assert gui._import_button.master is gui._list_actions
        assert gui._export_button.master is gui._list_actions

        window_left = root.winfo_rootx()
        window_right = window_left + root.winfo_width()
        for button in (gui._import_button, gui._export_button):
            assert button.winfo_rootx() >= window_left
            assert button.winfo_rootx() + button.winfo_width() <= window_right
    finally:
        if gui is not None:
            gui._v46_finish_close()
        elif root.winfo_exists():
            root.destroy()
