import importlib


def test_phase63_feature_flags_use_runtime_state_and_mirror_legacy():
    from cyoa_downloader_app.runtime import surface as legacy
    from cyoa_downloader_app.core.feature_flags import (
        _set_deep_scan_enabled,
        _set_selenium_enabled,
        _set_serve_enabled,
        _set_cheat_enabled,
    )
    from cyoa_downloader_app.runtime import state

    _set_deep_scan_enabled(False)
    _set_selenium_enabled(False)
    _set_serve_enabled(False)
    _set_cheat_enabled(False)

    assert state._DEEP_SCAN_ENABLED is False
    assert state._SELENIUM_ENABLED is False
    assert state._SERVE_ENABLED is False
    assert state._CHEAT_ENABLED is False
    assert legacy._DEEP_SCAN_ENABLED is False
    assert legacy._SELENIUM_ENABLED is False
    assert legacy._SERVE_ENABLED is False
    assert legacy._CHEAT_ENABLED is False

    _set_deep_scan_enabled(True)
    _set_selenium_enabled(True)
    _set_serve_enabled(True)
    _set_cheat_enabled(True)


def test_phase64_proxy_sessions_use_runtime_state_owner(monkeypatch):
    from cyoa_downloader_app.runtime import surface as legacy
    from cyoa_downloader_app.network.proxy import _get_active_proxy, _set_active_proxy
    from cyoa_downloader_app.network.sessions import _get_shared_session, _v465_reset_shared_sessions
    from cyoa_downloader_app.runtime import state

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    _set_active_proxy("http://127.0.0.1:9999", mode="manual")
    assert state._active_proxy == "http://127.0.0.1:9999"
    assert legacy._active_proxy == "http://127.0.0.1:9999"
    assert _get_active_proxy() == "http://127.0.0.1:9999"

    session = _get_shared_session()
    assert state._shared_session is session
    assert legacy._shared_session is session

    _set_active_proxy(None, mode="disabled")
    assert state._active_proxy is None
    assert legacy._active_proxy is None
    assert _get_active_proxy() is None
    _v465_reset_shared_sessions()
    assert state._shared_session is None
    assert legacy._shared_session is None


def test_phase65_archive_org_regex_owned_by_project_parse():
    from cyoa_downloader_app.runtime import surface as legacy
    from cyoa_downloader_app.download import orchestrator
    from cyoa_downloader_app.project import parse

    assert legacy._ARCHIVE_ORG_CYOA_RE is parse._ARCHIVE_ORG_CYOA_RE
    assert orchestrator._ARCHIVE_ORG_CYOA_RE is parse._ARCHIVE_ORG_CYOA_RE
    m = parse._ARCHIVE_ORG_CYOA_RE.search(
        "https://archive.org/download/CYOAZipArchive/Foo.2024.https~~~example.com~x.zip"
    )
    assert m and m.group(1).endswith(".zip")
