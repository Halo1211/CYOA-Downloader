"""Additional program audit regressions using contained offline fixtures."""

import builtins
import io
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest
import requests
from bs4 import BeautifulSoup

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.archive import validate_zip_archive
from cyoa_downloader_app.core.paths import _copytree_merge_safe
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import package
from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download.route_crawler import RouteCrawler
from cyoa_downloader_app.importers import batch
from cyoa_downloader_app.network import browser, fetch_base, proxy, runtime_capture
from cyoa_downloader_app.project import parse
from cyoa_downloader_app.runtime import state


@pytest.mark.parametrize("name", ["literal%2Fname.png", "literal%3Fname.png", "chapter#1.html", "100%25.png"])
def test_local_zip_preserves_literal_filenames(tmp_path, monkeypatch, name):
    source = tmp_path / "source"
    source.mkdir()
    (source / name).write_bytes(b"fixture")
    monkeypatch.chdir(tmp_path)
    archive_path = package.zip_temp_folder(str(source), "output")
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [name]
        assert archive.read(name) == b"fixture"


def test_local_zip_does_not_confuse_encoded_and_literal_names(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("art%20work.png", "art work.png"):
        (source / name).write_bytes(name.encode())
    monkeypatch.chdir(tmp_path)
    archive_path = package.zip_temp_folder(str(source), "output")
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {"art%20work.png", "art work.png"}


@pytest.mark.parametrize("name", ["art%20work.png", "chapter#1.html", "literal%2Fname.png", "100%25.png", "日本.png"])
def test_copy_of_local_assets_preserves_names(tmp_path, name):
    source = tmp_path / "source"
    source.mkdir()
    (source / name).write_bytes(b"fixture")
    destination = tmp_path / "destination"
    assert _copytree_merge_safe(str(source), str(destination)) == 1
    assert (destination / name).read_bytes() == b"fixture"


@pytest.mark.parametrize("suffix", ["chapter%23one", "chapter%2520one", "chapter%20one", "日本"])
def test_local_route_links_resolve_literal_filenames(tmp_path, suffix):
    start = "https://example.test/game/"
    target = start + suffix
    index = tmp_path / "index.html"
    index.write_text(f'<html><body><a href="{target}#choice">Next</a></body></html>', encoding="utf-8")
    crawler = RouteCrawler(SimpleNamespace(
        start_url=start, start_html_local=str(index), output_folder=str(tmp_path),
    ), ArchivePolicy(strategy="smart"))
    target_local = Path(crawler._route_local_path(target))
    target_local.parent.mkdir(parents=True)
    target_local.write_text("<html>Target</html>", encoding="utf-8")
    crawler._rewrite_route_links({start: str(index), target: str(target_local)})
    link = BeautifulSoup(index.read_text(encoding="utf-8"), "html.parser").find("a")["href"]
    parsed = urlparse(link)
    assert parsed.fragment == "choice"
    assert (index.parent / unquote(parsed.path)).resolve() == target_local.resolve()


@pytest.mark.parametrize("names", [("assets", "assets/image.png"), ("Assets/image.png", "assets"), ("assets/image.png", "ASSETS/other.png")])
def test_zip_rejects_conflicting_file_and_directory_names(names):
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        for name in names:
            archive.writestr(name, "fixture")
    # The last case is valid: shared parent directories differ only by case.
    if len(names[0].split("/")) == len(names[1].split("/")) == 2:
        assert validate_zip_archive(blob.getvalue())["members"] == 2
    else:
        with pytest.raises(ValueError, match="conflict"):
            validate_zip_archive(blob.getvalue())


def test_bad_deflate_member_does_not_hide_later_project():
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", '{"rows":[]}')
        archive.writestr("valid_project.json", '{"rows":[],"title":"Recovered"}')
    raw = bytearray(blob.getvalue())
    # First member's local header is 30 bytes plus its filename, no extra field.
    raw[30 + len("project.json")] = 0x07  # DEFLATE's reserved block type.
    extracted = parse.extract_project_from_archive_bytes(bytes(raw), "https://example.test/project.zip")
    assert json.loads(extracted)["title"] == "Recovered"


def test_local_text_import_contains_file_read_failure(tmp_path, monkeypatch):
    source = tmp_path / "queue.txt"
    source.write_text("https://example.test/game\n", encoding="utf-8")
    original_open = builtins.open

    def unavailable(path, *args, **kwargs):
        if str(path) == str(source):
            raise PermissionError("queue file is locked")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", unavailable)
    assert batch.import_queue_items_from_file(str(source)) == []


@pytest.mark.parametrize("encoding,bom", [
    ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
    ("utf-32-le", b"\xff\xfe\x00\x00"), ("utf-32-be", b"\x00\x00\xfe\xff"),
    ("utf-8-sig", b""),
])
def test_bom_decoding_does_not_require_optional_detectors(monkeypatch, encoding, bom):
    monkeypatch.setitem(sys.modules, "chardet", None)
    monkeypatch.setitem(sys.modules, "charset_normalizer", None)
    text = json.dumps({"rows": [], "title": "Pilihan 日本"}, ensure_ascii=False)
    assert parse.try_decode_bytes(bom + text.encode(encoding)) == text


@pytest.mark.parametrize("target,expected", [
    ("https://example.test/", "http://https-proxy.test:8080"),
    ("http://example.test/", "http://http-proxy.test:8080"),
    ("https://direct.test/", None),
])
def test_browser_fallback_honors_environment_proxy(monkeypatch, target, expected):
    monkeypatch.setattr(state, "_proxy_mode", "inherit_env")
    monkeypatch.setattr(state, "_active_proxy", None)
    for name in ("https_proxy", "http_proxy", "all_proxy", "no_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy.test:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://http-proxy.test:8080")
    monkeypatch.setenv("NO_PROXY", "direct.test")
    config = proxy._get_browser_proxy_config(target)
    assert (config or {}).get("server") == expected


def test_headless_browser_does_not_launch_after_cancellation(monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(browser, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(browser, "_get_browser_proxy_config", lambda url: pytest.fail("cancelled browser launched"))
    with pytest.raises(DownloadCancelledError):
        browser._fetch_headless("https://example.test/")


def test_reusable_browser_preserves_cancellation_after_body(monkeypatch):
    pytest.importorskip("playwright.sync_api")
    event = threading.Event()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(browser, "vpn_requirement_satisfied", lambda: True)
    session = browser.BrowserFetchSession()

    def body():
        event.set()
        return b"completed while cancelled"

    response = SimpleNamespace(ok=True, body=body, headers={}, status=200, url="https://example.test/")
    monkeypatch.setattr(session, "_ensure", lambda url: None)
    session._page = SimpleNamespace(goto=lambda *a, **kw: response)
    with pytest.raises(DownloadCancelledError):
        session.fetch("https://example.test/")


@pytest.mark.parametrize("stage", ["browser", "runtime"])
def test_runtime_capture_keeps_observed_assets_after_cleanup_failure(monkeypatch, stage):
    playwright = pytest.importorskip("playwright.sync_api")
    callbacks = {}
    downloaded = []
    response = SimpleNamespace(headers={"content-type": "image/png"}, url="https://example.test/image.png", status=200)

    def navigate(*args, **kwargs):
        callbacks["response"](response)

    def close():
        raise RuntimeError("browser already disconnected")

    page = SimpleNamespace(on=lambda name, callback: callbacks.update({name: callback}),
                           goto=navigate, url="https://example.test/", wait_for_timeout=lambda ms: None)
    context = SimpleNamespace(new_page=lambda: page, route=lambda *args: None)
    browser_runtime = SimpleNamespace(new_context=lambda **kw: context, close=close if stage == "browser" else lambda: None)
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser_runtime))

    class Runtime:
        def __enter__(self):
            return pw

        def __exit__(self, *args):
            if stage == "runtime":
                close()
            return False

    monkeypatch.setattr(playwright, "sync_playwright", Runtime)
    monkeypatch.setattr(runtime_capture, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(runtime_capture, "_get_browser_proxy_config", lambda url: {})
    monkeypatch.setattr(runtime_capture, "_incremental_scroll", lambda *args: 0)
    downloader = SimpleNamespace(_kind_from=lambda *args, **kw: "images",
                                 download_asset=lambda url, **kw: downloaded.append(url) or "image.png")
    result = runtime_capture._capture_runtime_assets_sync(downloader, ["https://example.test/"])
    assert result.discovered == [response.url]
    assert downloaded == [response.url]


@pytest.mark.parametrize("stage", ["body", "challenge"])
@pytest.mark.parametrize("error", [requests.exceptions.ContentDecodingError, requests.exceptions.ChunkedEncodingError,
                                   requests.exceptions.ConnectionError, requests.exceptions.Timeout])
def test_failed_http_body_releases_response(monkeypatch, stage, error):
    closed = []
    logger = SimpleNamespace(**{name: lambda *a, **kw: None for name in ("debug", "info", "warning", "error")})
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(logger=logger, _CLOUDFLARE_MODE="off"))
    monkeypatch.setattr(fetch_base, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda url: None)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda host: False)

    def broken_body():
        raise error("invalid compressed response")

    class Response:
        status_code = 200
        def __init__(self):
            self.headers = {}
        close = lambda self: closed.append(True)
        raise_for_status = lambda self: None

        @property
        def content(self):
            return broken_body()

    response = Response()
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **kw: SimpleNamespace(get=lambda *a, **kw: response))
    monkeypatch.setattr(fetch_base, "is_cloudflare_challenge", lambda r: broken_body() if stage == "challenge" else False)
    assert fetch_base.base_fetch_response("https://example.test/", as_bytes=True, stream=True) is None
    assert closed == [True]


@pytest.mark.skipif(os.environ.get("CYOA_RUNTIME_SMOKE") != "1", reason="optional live Chromium proxy test")
def test_live_browser_uses_inherited_http_proxy(monkeypatch):
    pytest.importorskip("playwright.sync_api")
    targets = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            targets.append(self.path)
            body = b"<!doctype html><html><body>Proxy fixture</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = browser.BrowserFetchSession()
    monkeypatch.setattr(state, "_proxy_mode", "inherit_env")
    monkeypatch.setattr(state, "_active_proxy", None)
    monkeypatch.setattr(browser, "vpn_requirement_satisfied", lambda: True)
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{server.server_port}")
    try:
        result = session.fetch("http://proxy-fixture.invalid/game")
        assert result is not None and b"Proxy fixture" in result.content
        assert "http://proxy-fixture.invalid/game" in targets
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
