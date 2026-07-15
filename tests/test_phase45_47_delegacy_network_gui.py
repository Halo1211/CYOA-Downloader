from pathlib import Path

import cyoa_downloader as facade

LEGACY_TEXT = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")


def test_phase45_feature_flags_are_real_module_and_removed_from_legacy():
    from cyoa_downloader_app.core import feature_flags

    assert facade._set_deep_scan_enabled is feature_flags._set_deep_scan_enabled
    assert facade._set_selenium_enabled is feature_flags._set_selenium_enabled
    assert facade._set_serve_enabled is feature_flags._set_serve_enabled
    assert facade._set_cheat_enabled is feature_flags._set_cheat_enabled
    for name in [
        "_set_deep_scan_enabled", "_set_selenium_enabled",
        "_set_serve_enabled", "_set_cheat_enabled",
    ]:
        assert f"def {name}(" not in LEGACY_TEXT


def test_phase46_network_core_helpers_removed_from_legacy():
    from cyoa_downloader_app.network import sessions, dns, throttle, cloudflare, fetch_base

    assert facade.create_retry_session is sessions.create_retry_session
    assert facade._set_active_dns is dns._set_active_dns
    assert facade._domain_throttle is throttle._domain_throttle
    assert facade._normalize_cloudflare_mode is cloudflare._normalize_cloudflare_mode
    assert fetch_base.base_fetch_response.__module__ == "cyoa_downloader_app.network.fetch_base"
    for name in [
        "create_retry_session", "_set_active_dns", "_domain_throttle",
        "_normalize_cloudflare_mode", "fetch_via_flaresolverr",
    ]:
        assert f"def {name}(" not in LEGACY_TEXT
    assert "Fetch a URL with automatic fallbacks:" not in LEGACY_TEXT
    assert "base_fetch_response" in Path("cyoa_downloader_app/network/fetch_base.py").read_text(encoding="utf-8")


def test_phase47_gui_telemetry_log_handler_removed_from_legacy():
    from cyoa_downloader_app.gui.telemetry_log import _V46TelemetryLogHandler

    assert facade._V46TelemetryLogHandler is _V46TelemetryLogHandler
    assert _V46TelemetryLogHandler.__module__ == "cyoa_downloader_app.gui.telemetry_log"
    assert "class _V46TelemetryLogHandler" not in LEGACY_TEXT
