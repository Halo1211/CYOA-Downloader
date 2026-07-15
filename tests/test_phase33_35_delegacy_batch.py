from __future__ import annotations

import json


def test_phase33_ai_calls_are_real_module_exports():
    import cyoa_downloader as public
    from cyoa_downloader_app.integrations import ai
    from cyoa_downloader_app.integrations import ai_calls

    assert ai._extract_single_ai_url is ai_calls._extract_single_ai_url
    assert ai._ai_call is ai_calls._ai_call
    assert public._extract_single_ai_url('"https://example.test/project.json"') == "https://example.test/project.json"
    assert public._extract_single_ai_url("javascript:alert(1)") is None
    assert public._extract_single_ai_url("NONE") is None


def test_phase34_offline_injector_exports_real_apply_function():
    import cyoa_downloader as public
    from cyoa_downloader_app.integrations.offline_viewers import injector

    assert public._apply_offline_viewer is injector._apply_offline_viewer
    assert injector._apply_offline_viewer.__module__.endswith("offline_viewers.injector")


def test_phase35_browser_helpers_are_domain_exports():
    import cyoa_downloader as public
    from cyoa_downloader_app.network import browser

    assert public._make_cookie_session is browser._make_cookie_session
    assert public._fetch_headless is browser._fetch_headless
    assert browser._make_cookie_session.__module__.endswith("network.browser")


def test_headless_asset_fetch_rejects_html_error_documents_when_requested():
    from cyoa_downloader_app.network.browser import _looks_like_error_document

    assert _looks_like_error_document(b"<!DOCTYPE html><html>404</html>", "")
    assert _looks_like_error_document(b'{"error":"not found"}', "application/octet-stream")
    assert not _looks_like_error_document(b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
