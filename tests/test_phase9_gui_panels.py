import cyoa_downloader
from cyoa_downloader_app.gui import app as gui_app
from cyoa_downloader_app.gui import panels
from cyoa_downloader_app.gui.panels import ai, batch, cache, cloudflare, credits, cyoa_manager, diagnostics, guide, offline_viewers, settings, updates


def test_phase9_panel_gate_is_bound_to_gui_class():
    cls = gui_app.CYOADownloaderGUI
    assert panels.attach_panel_methods(cls) is cls
    assert getattr(cls, "_cyoa_gui_panel_bind_order") == panels.PANEL_BIND_ORDER
    assert panels.bound_panel_methods(cls) == panels.panel_method_names()
    assert "batch" in panels.PANEL_BIND_ORDER
    assert "offline_viewers" in panels.PANEL_BIND_ORDER


def test_phase9_panel_modules_export_final_method_objects():
    cls = gui_app.CYOADownloaderGUI
    assert cls._cloudflare_panel is cloudflare._cloudflare_panel
    assert cls._batch_export_panel is batch._batch_export_panel
    assert cls._diagnostics_panel is diagnostics._diagnostics_panel
    assert cls._settings_maintenance_panel is settings._settings_maintenance_panel
    assert cls._cache_manager_panel is cache._cache_manager_panel
    assert cls._cyoa_manager_panel is cyoa_manager._cyoa_manager_panel
    assert cls._ai_settings_panel is ai._ai_settings_panel
    assert cls._check_updates_panel is updates._check_updates_panel
    assert cls._show_credits_panel is credits._show_credits_panel
    assert cls._show_feature_guide is guide._show_feature_guide
    assert cls._manage_offline_viewers is offline_viewers._manage_offline_viewers


def test_phase9_facade_exports_panel_gate():
    assert cyoa_downloader.attach_panel_methods is panels.attach_panel_methods
    assert cyoa_downloader.PANEL_BIND_ORDER == panels.PANEL_BIND_ORDER
    assert cyoa_downloader.bound_panel_methods(gui_app.CYOADownloaderGUI) == panels.panel_method_names()
    assert cyoa_downloader.gui_panel_batch._add_to_queue is batch._add_to_queue
