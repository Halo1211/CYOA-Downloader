from pathlib import Path

import cyoa_downloader as facade

LEGACY_TEXT = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")


def test_phase51_cli_main_body_moved_to_cli_module():
    from cyoa_downloader_app import cli

    assert facade.main is cli.main
    assert cli.main.__module__ == "cyoa_downloader_app.cli"
    assert "argparse.ArgumentParser" in Path("cyoa_downloader_app/cli.py").read_text(encoding="utf-8")
    assert "description=(\n            \"Download and process a CYOA project" not in LEGACY_TEXT


def test_phase51_fetch_wrapper_is_network_module():
    from cyoa_downloader_app.network import fetch

    assert facade.fetch_response is fetch.fetch_response
    assert fetch.fetch_response.__module__ == "cyoa_downloader_app.network.fetch"
    assert "def fetch_response(" not in LEGACY_TEXT
    assert "response_meta" in Path("cyoa_downloader_app/network/fetch.py").read_text(encoding="utf-8")


def test_phase52_small_gui_widget_helpers_removed_from_legacy():
    from cyoa_downloader_app.gui import widgets

    assert facade._v25_safe_after_widget is widgets._v25_safe_after_widget
    assert facade._v27_safe_after is widgets._v27_safe_after
    assert widgets._v25_safe_after_widget.__module__ == "cyoa_downloader_app.gui.widgets"
    for name in [
        "_v25_safe_after", "_v25_safe_after_widget", "_v25_center_window",
        "_v27_ai_provider_values", "_v27_safe_after", "_v27_open_path",
    ]:
        assert f"def {name}(" not in LEGACY_TEXT


def test_phase52_v24_patch_bodies_moved_to_patch_module():
    from cyoa_downloader_app.gui import final_behaviors

    assert facade._v24_show_results is final_behaviors._v24_show_results
    assert final_behaviors._v24_show_results.__module__ == "cyoa_downloader_app.gui.final_behaviors"
    for name in [
        "_v24_card", "_v24_badge", "_v24_show_results",
        "_v24_batch_update_panel", "_v24_diagnostics_panel", "_v24_add_url_to_queue",
    ]:
        assert f"def {name}(" not in LEGACY_TEXT


def test_phase53_v27_panel_bodies_moved_to_patch_module():
    from cyoa_downloader_app.gui import final_behaviors

    assert facade._v27_cache_manager_panel is final_behaviors._v27_cache_manager_panel
    assert facade._v27_check_updates_panel is final_behaviors._v27_check_updates_panel
    assert facade._v27_ai_settings_panel is final_behaviors._v27_ai_settings_panel
    assert final_behaviors._v27_ai_settings_panel.__module__ == "cyoa_downloader_app.gui.final_behaviors"
    for name in ["_v27_cache_manager_panel", "_v27_check_updates_panel", "_v27_ai_settings_panel"]:
        assert f"def {name}(" not in LEGACY_TEXT

