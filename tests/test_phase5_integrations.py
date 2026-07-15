import cyoa_downloader
from cyoa_downloader_app.integrations import ai as ai_mod
from cyoa_downloader_app.integrations import cyoa_manager as mgr_mod
from cyoa_downloader_app.integrations import gallery_dl as gdl_mod
from cyoa_downloader_app.integrations import itch as itch_mod
from cyoa_downloader_app.integrations import plugins as plugins_mod
from cyoa_downloader_app.integrations.offline_viewers import registry as viewer_registry
from cyoa_downloader_app.integrations.offline_viewers import archive_store
from cyoa_downloader_app.integrations.offline_viewers import iccplus
from cyoa_downloader_app.integrations.offline_viewers import injector


def test_phase5_facade_integration_names_still_match_modules():
    assert cyoa_downloader._normalize_ai_provider is ai_mod._normalize_ai_provider
    assert cyoa_downloader.AIUsageBudget is ai_mod.AIUsageBudget
    assert cyoa_downloader.add_to_cyoa_manager is mgr_mod.add_to_cyoa_manager
    assert cyoa_downloader.register_asset_scanner is plugins_mod.register_asset_scanner
    assert cyoa_downloader.run_asset_scanner_plugins is plugins_mod.run_asset_scanner_plugins
    assert cyoa_downloader._fetch_via_gallery_dl is gdl_mod._fetch_via_gallery_dl
    assert cyoa_downloader.detect_itch_backend is itch_mod.detect_itch_backend
    assert cyoa_downloader.register_offline_viewer is viewer_registry.register_offline_viewer
    assert cyoa_downloader._extract_iccplus_subviewers is archive_store._extract_iccplus_subviewers
    assert cyoa_downloader._apply_iccplus_viewer_config_to_html is iccplus._apply_iccplus_viewer_config_to_html
    assert cyoa_downloader._apply_offline_viewer is injector._apply_offline_viewer


def test_phase5_ai_and_ssrf_smoke():
    assert ai_mod._normalize_ai_provider("Open AI") == "openai"
    assert ai_mod._default_ai_model("ollama")
    assert ai_mod._host_is_internal("127.0.0.1") is True
    assert ai_mod._host_is_internal("example.com") is False
    assert ai_mod._sanitize_ai_candidate_url("javascript:alert(1)") is None


def test_phase5_plugin_registry_smoke():
    name = "phase5_test_scanner"
    try:
        plugins_mod._ASSET_SCANNER_PLUGINS.unregister(name)
    except Exception:
        pass

    def scanner(_text, _file_url, _base_url, _file_ext=".js"):
        return {"https://example.com/asset.png"}

    plugins_mod.register_asset_scanner(name, scanner)
    try:
        assert "https://example.com/asset.png" in plugins_mod.run_asset_scanner_plugins("", "", "")
    finally:
        plugins_mod._ASSET_SCANNER_PLUGINS.unregister(name)


def test_phase5_itch_and_offline_viewer_smoke():
    assert itch_mod._is_itch_url("https://example.itch.io/game") is True
    assert itch_mod._is_itch_url("https://example.com/game") is False
    assert isinstance(viewer_registry._load_viewers_manifest(), dict)
