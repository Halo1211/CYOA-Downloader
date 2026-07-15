import cyoa_downloader
from cyoa_downloader_app.gui import app as gui_app
from cyoa_downloader_app.gui import patches as patch_mod
from pathlib import Path


def test_phase8_patch_pipeline_is_centralized_and_ordered():
    cls = gui_app.CYOADownloaderGUI
    assert patch_mod.apply_gui_patches(cls) is cls
    assert patch_mod.applied_patch_order(cls) == patch_mod.PATCH_ORDER
    assert patch_mod.PATCH_ORDER == ("v24", "v25", "v27", "v46", "v462", "v463", "v465", "v466")
    assert getattr(cls, "_cyoa_gui_patch_pipeline_mode") == "composed-bootstrap"


def test_phase8_facade_exports_patch_gate():
    assert cyoa_downloader.apply_gui_patches is patch_mod.apply_gui_patches
    assert cyoa_downloader.PATCH_ORDER == patch_mod.PATCH_ORDER
    assert cyoa_downloader.applied_patch_order(gui_app.CYOADownloaderGUI) == patch_mod.PATCH_ORDER


def test_phase8_final_gui_patch_surface_is_present():
    cls = gui_app.CYOADownloaderGUI
    missing = patch_mod._verify_patch_surface(cls, strict=False)
    assert missing == []
    for _patch_id, names in patch_mod.expected_patch_surface().items():
        for name in names:
            assert hasattr(cls, name)
    assert cls._apply_theme.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_apply_theme_base")
    assert cls._setup_ui.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_setup_ui_base")
    assert cls.__init__.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_init_base")
    for name in [
        "_start",
        "_worker",
        "_done",
        "_v46_enqueue_progress",
        "_v46_set_event_sink",
        "_v46_cancel",
        "_v46_on_close",
        "_v46_finish_close",
        "_v46_copy_error",
        "_record_speed_bytes",
        "_on_ytdlp_progress",
        "_start_speed_graph",
        "_stop_speed_graph",
        "_v46_poll_progress",
        "_v46_render_progress",
        "_v46_draw_speed_graph",
        "_v46_apply_progress_visibility",
        "_v46_toggle_progress_panel",
        "_v46_install_url_menu",
        "_v462_refresh_responsive_layout",
        "_v463_arrange_progress_and_log",
        "_v463_rebuild_progress_workspace",
        "_show_results",
        "_batch_update_panel",
        "_diagnostics_panel",
        "_add_url_to_queue",
        "_cloudflare_panel",
        "_manage_offline_viewers",
        "_cache_manager_panel",
        "_check_updates_panel",
        "_ai_settings_panel",
    ]:
        assert getattr(cls, name).__module__ == "cyoa_downloader_app.gui.app"


def test_phase8_bootstrap_uses_central_method_binding():
    source = Path("cyoa_downloader_app/gui/bootstrap.py").read_text(encoding="utf-8")
    assert "CYOADownloaderGUI._setup_ui =" not in source
    assert "CYOADownloaderGUI.__init__ =" not in source
    assert "def _bind_methods(" in source


def test_phase8_public_gui_class_no_longer_exposes_patch_module_methods():
    leaked = {
        name: getattr(obj, "__module__", type(obj).__module__)
        for name, obj in vars(gui_app.CYOADownloaderGUI).items()
        if callable(obj) and getattr(obj, "__module__", "").startswith("cyoa_downloader_app.gui.patch")
    }
    assert leaked == {}
