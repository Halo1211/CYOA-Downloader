import importlib

from cyoa_downloader_app.core.url_utils import _directory_base_url
from cyoa_downloader_app.project import cyoap_vue


class FakeResponse:
    def __init__(self, content, status_code=200, content_type="application/json", encoding=None):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.encoding = encoding
        self.closed = False

    def close(self):
        self.closed = True


def test_directory_base_url_preserves_extensionless_routes_and_strips_documents():
    assert _directory_base_url("https://Example.com/game/abc") == "https://example.com/game/abc/"
    assert _directory_base_url("https://example.com/game/index.html?x=1") == "https://example.com/game/"
    assert _directory_base_url("https://example.com/game/") == "https://example.com/game/"


def test_scan_cyoap_assets_collects_nested_image_and_media_values():
    images = set()
    media = set()
    payload = {
        "nodes": [
            {"image": "img/card.png", "children": [{"bgm": "audio/theme.mp3"}]},
            {"custom": "https://cdn.example/video/intro.webm"},
            {"image": "data:image/png;base64,ignored"},
        ]
    }

    cyoap_vue._scan_cyoap_assets(payload, images, media)

    assert "img/card.png" in images
    assert "audio/theme.mp3" in media
    assert "https://cdn.example/video/intro.webm" in media
    assert all(not value.startswith("data:") for value in images | media)


def test_probe_cyoap_vue_structure_validates_json_types(monkeypatch):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        if url.endswith("platform.json"):
            return FakeResponse(b'{"title":"demo"}')
        if url.endswith("nodes/list.json"):
            return FakeResponse(b'[]')
        raise AssertionError(url)

    monkeypatch.setattr(cyoap_vue, "fetch_response", fake_fetch)

    assert cyoap_vue._probe_cyoap_vue_structure("https://example.com/game/123") is True
    assert calls == [
        "https://example.com/game/123/dist/platform.json",
        "https://example.com/game/123/dist/nodes/list.json",
    ]


def test_probe_cyoap_vue_structure_rejects_html_fallback(monkeypatch):
    def fake_fetch(url, **kwargs):
        return FakeResponse(b"<html>SPA fallback</html>", content_type="text/html")

    monkeypatch.setattr(cyoap_vue, "fetch_response", fake_fetch)

    assert cyoap_vue._probe_cyoap_vue_structure("https://example.com/game/123") is False


def test_phase16_symbols_are_owned_by_domain_modules():
    import cyoa_downloader as facade

    assert facade._scan_cyoap_assets.__module__ == "cyoa_downloader_app.project.cyoap_vue"
    assert facade._probe_cyoap_vue_structure.__module__ == "cyoa_downloader_app.project.cyoap_vue"
    assert facade._directory_base_url.__module__ == "cyoa_downloader_app.core.url_utils"
