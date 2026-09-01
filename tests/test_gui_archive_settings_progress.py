from __future__ import annotations

import inspect

from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.gui.app import CYOADownloaderGUI
from cyoa_downloader_app.gui.final_behaviors import _v463_progress_detail_height


def test_javascript_archive_policy_is_exposed_in_settings_center():
    source = inspect.getsource(CYOADownloaderGUI._settings_maintenance_panel)

    assert "JavaScript Archive Policy" in source
    assert "Kebijakan Arsip JavaScript" in source
    assert "archive_runtime_max_pages" in source
    assert "archive_settle_time_ms" in source
    assert "archive_no_progress_rounds" in source
    assert "Every number is a safety cap" in source
    assert "Semua angka adalah batas pengaman" in source
    assert 'self._show_feature_guide("settings")' in source


def test_feature_entry_point_routes_to_settings_without_a_second_window():
    source = inspect.getsource(CYOADownloaderGUI._toggles_panel)
    assert "self._settings_maintenance_panel()" in source


def test_persistent_feature_controls_are_defined_in_settings_center():
    source = inspect.getsource(CYOADownloaderGUI._settings_maintenance_panel)
    for key in (
        '"deep_scan_enabled"',
        '"selenium_enabled"',
        '"serve_enabled"',
        '"cheat_enabled"',
        '"gallery_dl_mode"',
        '"itch_enabled"',
    ):
        assert key in source
    assert "Download features" in source
    assert "Fitur download" in source


def test_retry_actions_are_kept_in_one_stable_toolbar_group():
    source = inspect.getsource(CYOADownloaderGUI._setup_ui_base)
    assert "retry_group" in source
    assert source.index('"Retry Assets"') < source.index('"Retry Images"') < source.index('"Retry Audio"')
    assert 'retry_group.pack(side="left"' in source
    assert 'left_tools.pack(side="left"' in source
    responsive = inspect.getsource(final_behaviors._v462_apply_small_screen_layout)
    assert "retry_group" in responsive
    assert "for child in (retry_group, left_tools)" in responsive


def test_settings_center_uses_a_clean_compact_header():
    source = inspect.getsource(CYOADownloaderGUI._settings_maintenance_panel)
    assert "Settings / Maintenance" in source
    assert "CTkScrollableFrame" in source
    assert "SETTINGS CENTER" not in source


def test_settings_center_routes_to_modern_single_window_dashboard():
    entry_source = inspect.getsource(CYOADownloaderGUI._settings_maintenance_panel)
    dashboard_source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)

    assert "return self._settings_dashboard_panel()" in entry_source
    assert "sidebar" in dashboard_source
    assert "page_host" in dashboard_source
    assert "search_entry" in dashboard_source
    assert "Integrations" in dashboard_source
    assert "Maintenance" in dashboard_source
    assert "CTkTabview" not in dashboard_source
    assert "grab_set" not in dashboard_source


def test_network_validation_is_offline_and_exposes_dns_presets():
    source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)

    assert "Validate offline" in source
    assert "Tidak ada request eksternal" in source
    assert "DNS_PRESETS.keys()" in source
    assert "cyoa.cafe/favicon.svg" not in source
    assert "fetch_response(" not in source


def test_settings_advanced_workflows_are_embedded_instead_of_opening_panels():
    dashboard_source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)

    for inline_builder in (
        "self._settings_inline_ai",
        "self._settings_inline_cloudflare",
        "self._settings_inline_cache",
        "self._settings_inline_viewers",
    ):
        assert inline_builder in dashboard_source

    for legacy_window_callback in (
        "self._ai_settings_panel",
        "self._cloudflare_panel",
        "self._cache_manager_panel",
        "self._manage_offline_viewers",
    ):
        assert legacy_window_callback not in dashboard_source

    assert 'button_text=("Edit JSON"' in dashboard_source
    assert 'button_text=("Export…"' in dashboard_source


def test_inline_settings_builders_keep_complete_controls_available():
    ai_source = inspect.getsource(CYOADownloaderGUI._settings_inline_ai)
    cloudflare_source = inspect.getsource(CYOADownloaderGUI._settings_inline_cloudflare)
    cache_source = inspect.getsource(CYOADownloaderGUI._settings_inline_cache)
    viewers_source = inspect.getsource(CYOADownloaderGUI._settings_inline_viewers)

    assert "ai_provider" in ai_source
    assert "ai_key_storage" in ai_source
    assert "_ai_call" in ai_source
    assert "_set_cloudflare_config" in cloudflare_source
    assert "flaresolverr_test_connection" in cloudflare_source
    assert "image_cache_max_mb" in cache_source
    assert "_clear_image_cache" in cache_source
    assert "register_offline_viewer" in viewers_source
    assert "unregister_offline_viewer" in viewers_source


def test_inline_ai_form_preserves_provider_specific_credentials_and_models():
    source = inspect.getsource(CYOADownloaderGUI._settings_inline_ai)

    assert '_resolve_ai_api_key(\n                storage="plain"' in source
    assert "def _provider_changed" in source
    assert "_ai_model_options(provider)" in source
    assert "model_var.set(_default_ai_model(provider))" in source
    assert 'if storage == "plain" and provider != "ollama" and not key:' in source
    assert "never carry one provider's" in source


def test_inline_cloudflare_refreshes_header_from_the_saved_form_value():
    source = inspect.getsource(CYOADownloaderGUI._settings_inline_cloudflare)

    assert "_display_cloudflare_mode(mode_var.get())" in source
    assert "_display_cloudflare_mode(_CLOUDFLARE_MODE)" not in source


def test_download_page_combines_general_features_and_archive():
    dashboard_source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)

    assert '("Download" if is_en else "Download")' in dashboard_source
    assert "archive = general" in dashboard_source
    assert "features = general" in dashboard_source
    assert 'network = _page(tab_names[1])' in dashboard_source
    assert 'integrations = _page(tab_names[2])' in dashboard_source
    assert 'tools = _page(tab_names[3])' in dashboard_source
    assert '("General" if is_en else "Umum")' not in dashboard_source


def test_download_page_orders_common_controls_before_advanced_archive_policy():
    source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)

    assert "features_title_row = auto_row + 2" in source
    assert "credentials_title_row = features_card_row + 4" in source
    assert "archive_title_row = credentials_card_row + 3" in source
    assert "key_card.grid(row=credentials_card_row + 1" in source
    assert "Access & Credentials" in source
    assert "Akses & Kredensial" in source
    assert "help_row" not in source
    assert 'footer, text=("Open Guide"' not in source


def test_archive_policy_form_is_grouped_into_readable_responsive_columns():
    source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)

    assert 'uniform="archive_setting"' in source
    assert "for col in range(2)" in source
    assert "Archive behavior" in source
    assert "Discovery limits" in source
    assert "Browser runtime limits" in source
    assert "Stop after this many rounds find nothing new" in source
    assert "Every number is a safety cap, not a download target" in source


def test_settings_dashboard_explains_advanced_and_common_controls():
    source = inspect.getsource(CYOADownloaderGUI._settings_dashboard_panel)
    ai_source = inspect.getsource(CYOADownloaderGUI._settings_inline_ai)
    cloudflare_source = inspect.getsource(CYOADownloaderGUI._settings_inline_cloudflare)
    cache_source = inspect.getsource(CYOADownloaderGUI._settings_inline_cache)

    archive_hints = (
        "Auto selects the lightest complete pipeline",
        "Safe permits guarded scroll/click",
        "Hard cap for same-origin story routes",
        "Route hops from entry; 0 means entry only",
        "Maximum pages rendered by the browser engine",
        "Wait after load/action for late assets",
        "Maximum incremental lazy-load scrolls",
        "Maximum allowlisted clicks per runtime page",
        "Stop after this many rounds find nothing new",
    )
    assert all(hint in source for hint in archive_hints)
    assert "Chooses Folder or ZIP whenever output mode is Auto" in source
    assert "Used by yt-dlp for login or age-restricted media" in source
    assert "Service that handles AI requests" in ai_source
    assert "Controls when AI recovery may run" in ai_source
    assert "Challenge-solving behavior for protected websites" in cloudflare_source
    assert "Controls challenge-cookie reuse" in cloudflare_source
    assert "clearing it does not remove completed output" in cache_source


def test_embedded_help_documents_offline_network_validation_and_presets():
    source = inspect.getsource(CYOADownloaderGUI._show_feature_guide)

    assert "Advanced Network options" in source
    assert "Cloudflare 1.1.1.1/1.0.0.1" in source
    assert "DoH uses HTTPS" in source
    assert "DoT uses port 853" in source
    assert "It never downloads a favicon or probes CYOA.CAFE" in source
    assert "test the actual HTTPS route" not in source


def test_inline_forms_use_responsive_columns_without_large_middle_gaps():
    ai_source = inspect.getsource(CYOADownloaderGUI._settings_inline_ai)
    cloudflare_source = inspect.getsource(CYOADownloaderGUI._settings_inline_cloudflare)
    cache_source = inspect.getsource(CYOADownloaderGUI._settings_inline_cache)

    assert 'uniform="ai_setting"' in ai_source
    assert 'columnspan=2, sticky="ew"' in ai_source
    assert 'uniform="cf_setting"' in cloudflare_source
    assert "form.grid_columnconfigure(0, weight=1" in cloudflare_source
    assert "form.grid_columnconfigure(1, weight=1" in cloudflare_source
    assert "controls.grid_columnconfigure(5, weight=1)" in cache_source
    assert "controls.grid_columnconfigure(0, weight=1)" not in cache_source


def test_expanded_progress_restores_main_panels_instead_of_focus_takeover():
    source = inspect.getsource(final_behaviors._v463_apply_progress_visibility)
    assert "panel.grid()" in source
    assert "focus_mode" not in source
    assert "_v463_set_queue_density" in source


def test_progress_detail_frame_uses_one_compact_content_height():
    assert _v463_progress_detail_height(False, 1080, 400) == 0
    assert _v463_progress_detail_height(True, 800, 400) == 148
    assert _v463_progress_detail_height(True, 1440, 700) == 148


def test_diagnostics_center_keeps_install_steps_in_report_text():
    source = inspect.getsource(final_behaviors._v24_diagnostics_panel)
    assert "Install Guide" not in source
    assert "Copy" in source


def test_final_gui_labels_do_not_contain_utf8_mojibake():
    source = inspect.getsource(final_behaviors)
    for broken_prefix in ("Ã", "â", "Â", "ð"):
        assert broken_prefix not in source
    assert " — " in source
    assert "…" in source
