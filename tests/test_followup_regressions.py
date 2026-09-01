import json
from pathlib import Path
from types import SimpleNamespace

import requests

from cyoa_downloader_app.integrations import ai_core
from cyoa_downloader_app.network import fetch_base
from cyoa_downloader_app.project import cyoap_vue


def _response(url: str, body: bytes, content_type: str) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response.headers["Content-Type"] = content_type
    response._content = body
    return response


def test_cyoap_extensionless_entry_path_and_internal_asset_guard(monkeypatch, tmp_path):
    start_url = "https://example.invalid/game"
    calls = []

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if url.endswith("platform.json"):
            return _response(
                url,
                json.dumps({"image": "http://127.0.0.1:9/secret.png"}).encode(),
                "application/json",
            )
        if url.endswith("list.json"):
            return _response(url, b"[]", "application/json")
        if url == start_url:
            return _response(url, b"<html><body>ok</body></html>", "text/html")
        return _response(url, b"fake-image", "image/png")

    monkeypatch.setattr(ai_core, "_host_resolves_internal", lambda host: host == "127.0.0.1")
    monkeypatch.setattr(cyoap_vue, "fetch_response", fake_fetch)
    monkeypatch.setattr(cyoap_vue, "get_headers_for_url", lambda _url: {})

    assert cyoap_vue.try_download_cyoap_vue_site(
        start_url, str(tmp_path), website_zip_output=False, max_workers=1
    ) is True
    assert (tmp_path / "game" / "index.html").is_file()
    assert not any("127.0.0.1" in url for url in calls)


def test_flaresolverr_error_obeys_return_error_response(monkeypatch):
    class Logger:
        warning = error = info = debug = staticmethod(lambda *_a, **_k: None)

    class Session:
        def get(self, url, **_kwargs):
            response = requests.Response()
            response.status_code = 500
            response.url = url
            response.headers["Content-Type"] = "text/plain"
            response._content = b"server error"
            return response

    def fake_flaresolverr(url, **_kwargs):
        response = requests.Response()
        response.status_code = 500
        response.url = url
        response.headers["Content-Type"] = "text/plain"
        response._content = b"server error"
        return response

    monkeypatch.setattr(
        fetch_base,
        "legacy",
        lambda: SimpleNamespace(
            logger=Logger(),
            _CLOUDFLARE_MODE="flaresolverr",
            _CLOUDFLARE_PRIORITY="flaresolverr_first",
        ),
    )
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **_kwargs: Session())
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda _host: False)
    monkeypatch.setattr(fetch_base, "fetch_via_flaresolverr", fake_flaresolverr)

    assert fetch_base.base_fetch_response("https://example.invalid/page") is None
    response = fetch_base.base_fetch_response(
        "https://example.invalid/page", return_error_response=True
    )
    assert response is not None and response.status_code == 500
