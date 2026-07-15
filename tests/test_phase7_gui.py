import cyoa_downloader
from cyoa_downloader_app.gui import app as gui_app
from cyoa_downloader_app.gui import preview_server
from cyoa_downloader_app.gui import theme as gui_theme
from cyoa_downloader_app.gui import widgets as gui_widgets
from cyoa_downloader_app.gui.patches import apply_gui_patches


def test_phase7_gui_facade_names_still_match_modules():
    assert cyoa_downloader.CYOADownloaderGUI is gui_app.CYOADownloaderGUI
    assert cyoa_downloader.launch_gui is gui_app.launch_gui
    assert cyoa_downloader.GUILogHandler is gui_widgets.GUILogHandler
    assert cyoa_downloader._v25_safe_after_widget is gui_widgets._v25_safe_after_widget
    assert cyoa_downloader.userscript_integration_report is preview_server.userscript_integration_report
    assert cyoa_downloader._normalize_theme_mode is gui_theme._normalize_theme_mode
    assert cyoa_downloader._resolve_theme_is_dark is gui_theme._resolve_theme_is_dark


def test_phase7_gui_class_is_already_patched_and_patch_hook_is_stable():
    cls = gui_app.CYOADownloaderGUI
    assert apply_gui_patches(cls) is cls
    # These patched/final methods are installed by the legacy import order; the
    # bridge must not resurrect an earlier implementation.
    assert hasattr(cls, "_setup_ui")
    assert hasattr(cls, "_v46_poll_progress")
    assert hasattr(cls, "_safe_message")


def test_phase7_preview_token_helpers_round_trip():
    token = preview_server._new_preview_token()
    try:
        assert preview_server._current_preview_token() == token
        assert preview_server._preview_token_valid(token) is True
        assert preview_server._preview_token_valid("wrong-token") is False
    finally:
        preview_server._clear_preview_token()

