"""Offline dependency-base and traversal regressions for deep website scans."""

import base64
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest
import requests

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import image_pipeline, website
from cyoa_downloader_app.network.budget import RequestBudget

START = "https://example.test/game/"
CDN = "https://cdn.example.test/theme/"


@pytest.fixture
def scan(tmp_path, monkeypatch):
    calls = []
    bodies = {}
    monkeypatch.setattr(image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", image_pipeline._scan_file_for_assets)

    def fetch(url, **_kwargs):
        calls.append(url)
        item = bodies.get(url)
        if item is None:
            return None
        text, content_type, effective = item
        result = requests.Response()
        result.status_code = 200
        result.url = effective or url
        result.headers["Content-Type"] = content_type
        result._content = text.encode("utf-8")
        result._content_consumed = True
        return result

    monkeypatch.setattr(image_pipeline, "fetch_response", fetch)

    def run(**kwargs):
        return image_pipeline._deep_scan_and_download_assets(
            str(tmp_path), START, str(tmp_path), max_workers=1, ai_mode="off", **kwargs,
        )

    return SimpleNamespace(run=run, calls=calls, bodies=bodies, fetch=fetch)


@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.parametrize("http2", [False, True])
def test_saved_stylesheets_keep_original_remote_dependency_base(scan, tmp_path, monkeypatch, redirect, http2):
    if http2:
        httpx = pytest.importorskip("httpx")
        budget = RequestBudget(total=1, per_host=1)
        monkeypatch.setattr(image_pipeline, "HTTP_BUDGET", budget)

        class Client:
            def get(self, url, **kwargs):
                assert budget.snapshot()["active"] == 1
                result = scan.fetch(url, **kwargs)
                if result is not None:
                    result.url = httpx.URL(result.url)
                return result

            def close(self):
                pass

        monkeypatch.setattr(image_pipeline.runtime_state, "_HTTP2_ENABLED", True)
        monkeypatch.setattr(httpx, "Client", lambda **_kw: Client())
    entry = START + "style.css" if redirect else CDN + "main.css"
    (tmp_path / "seed.js").write_text(f'const theme = "{entry}";', "utf-8")
    scan.bodies.update({
        entry: ('@import "nested.css"; body{background:url(icon.png)}', "text/css", CDN + "main.css"),
        CDN + "nested.css": ('body{background:url(second.png)}', "text/css", None),
        CDN + "icon.png": ("PNG-one", "image/png", None),
        CDN + "second.png": ("PNG-two", "image/png", None),
    })
    sources = {}
    results = scan.run(source_urls=sources)
    assert set(results) == {entry, CDN + "nested.css", CDN + "icon.png", CDN + "second.png"}
    assert set(scan.calls) == set(results)
    assert all((tmp_path / relative).is_file() for relative in results.values())
    assert sources[str(tmp_path / results[entry])] == CDN + "main.css"
    if http2:
        assert budget.snapshot()["active"] == 0


def test_http2_failure_releases_shared_slot_before_requests_fallback(scan, tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    budget = RequestBudget(total=1, per_host=1)
    monkeypatch.setattr(image_pipeline, "HTTP_BUDGET", budget)
    monkeypatch.setattr(image_pipeline.runtime_state, "_HTTP2_ENABLED", True)
    class Client:
        def get(self, *_a, **_kw):
            assert budget.snapshot()["active"] == 1
            raise httpx.ConnectError("fixture")
        def close(self):
            pass
    monkeypatch.setattr(httpx, "Client", lambda **_kw: Client())
    url = START + "image.png"
    (tmp_path / "seed.js").write_text(f'const image = "{url}";', "utf-8")
    scan.bodies[url] = ("PNG", "image/png", None)
    def fallback(*args, **kwargs):
        assert budget.snapshot()["active"] == 0
        return scan.fetch(*args, **kwargs)
    monkeypatch.setattr(image_pipeline, "fetch_response", fallback)
    assert set(scan.run()) == {url}
    assert budget.snapshot()["active"] == 0


@pytest.mark.parametrize("folder", ["styles#one", "styles%23one", "styles one"])
def test_existing_local_folder_names_are_encoded_when_building_source_urls(scan, tmp_path, folder):
    directory = tmp_path / folder
    directory.mkdir()
    (directory / "main.css").write_text("body{background:url(hero.png)}", "utf-8")
    expected = START + quote(folder + "/hero.png", safe="/")
    scan.bodies[expected] = ("PNG", "image/png", None)
    assert set(scan.run()) == {expected}
    assert scan.calls == [expected]


def test_backup_tree_is_not_an_active_dependency_source(scan, tmp_path):
    backup = tmp_path / "__original_site__"
    backup.mkdir()
    (backup / "old.css").write_text("body{background:url(stale.png)}", "utf-8")
    (tmp_path / "active.css").write_text("body{color:red}", "utf-8")
    assert scan.run() == {}
    assert scan.calls == []


def test_active_reference_to_backup_does_not_overwrite_recovery_file(scan, tmp_path):
    backup = tmp_path / "__original_site__"
    backup.mkdir()
    existing = backup / "hero.png"
    existing.write_bytes(b"recovery copy")
    (tmp_path / "seed.css").write_text('body{background:url("__original_site__/hero.png")}', "utf-8")
    scan.bodies[START + "__original_site__/hero.png"] = ("new PNG", "image/png", None)
    assert scan.run() == {}
    assert scan.calls == []
    assert existing.read_bytes() == b"recovery copy"


def test_one_directory_walk_per_scan_round(scan, tmp_path, monkeypatch):
    (tmp_path / "seed.css").write_text("body{background:url(hero.png)}", "utf-8")
    scan.bodies[START + "hero.png"] = ("PNG", "image/png", None)
    original_walk = image_pipeline.os.walk
    walks = []

    def counted(*args, **kwargs):
        walks.append(str(args[0]))
        yield from original_walk(*args, **kwargs)

    monkeypatch.setattr(image_pipeline.os, "walk", counted)
    assert set(scan.run()) == {START + "hero.png"}
    assert len(walks) == 2


def test_repeated_known_urls_are_canonicalized_once_per_scan(scan, tmp_path, monkeypatch):
    urls = {START + f"image{index}.png" for index in range(30)}
    text = "const images = " + __import__("json").dumps(sorted(urls)) + ";"
    for index in range(20):
        (tmp_path / f"bundle{index}.js").write_text(text, "utf-8")
    parsed = []
    native = image_pipeline.urlparse

    def parse(url, *args, **kwargs):
        if url in urls:
            parsed.append(url)
        return native(url, *args, **kwargs)

    monkeypatch.setattr(image_pipeline, "urlparse", parse)
    assert scan.run(skip_urls=urls) == {}
    assert scan.calls == []
    assert len(parsed) == len(urls)
    assert scan.run(skip_urls=urls) == {}
    assert len(parsed) == 2 * len(urls), "canonical cache escaped its scan/request context"


def test_generated_image_metadata_survives_later_bfs_sources(scan, tmp_path, monkeypatch):
    native = image_pipeline.run_asset_scanner_plugins

    def plugin(text, *args):
        found = native(text, *args)
        return found | ({START + "constructors.js"} if "constructors.js" in text else set())

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", plugin)
    (tmp_path / "seed.js").write_text(
        """'li_' + group + '_' + name.toLowerCase().replaceAll(' ', '');
        const template = 'img/${entry.id}.webp';
        const script = "https://example.test/game/constructors.js";""",
        "utf-8",
    )
    scan.bodies[START + "constructors.js"] = (
        "class Thing extends Entry {\nsuper(name, 'item');\n}\nnew Thing('A/B Name');",
        "application/javascript", None,
    )
    scan.bodies[START + "img/li_item_abname.webp"] = ("PNG", "image/png", None)
    assert set(scan.run()) == {START + "constructors.js", START + "img/li_item_abname.webp"}


def test_existing_relocated_asset_accepts_its_recorded_source_url(scan, tmp_path):
    css = tmp_path / "external/assets/theme_hash.css"
    css.parent.mkdir(parents=True)
    css.write_text("body{background:url(hero.png)}", "utf-8")
    scan.bodies[CDN + "hero.png"] = ("PNG", "image/png", None)
    assert set(scan.run(source_urls={str(css): CDN + "main.css"})) == {CDN + "hero.png"}
    assert scan.calls == [CDN + "hero.png"]


def test_recorded_extensionless_css_base_keeps_existing_root_recovery(scan, tmp_path):
    css = tmp_path / "styles/main.css"
    css.parent.mkdir()
    css.write_text("body{background:url(hero.png)}", "utf-8")
    scan.bodies[START + "hero.png"] = ("PNG", "image/png", None)
    results = scan.run(source_urls={str(css): START + "assets/style?theme=dark"})
    assert set(results) == {START + "hero.png"}
    assert scan.calls == [START + "assets/hero.png", START + "hero.png"]


def test_cancel_between_scanned_files_stops_without_reading_next_file(scan, tmp_path, monkeypatch):
    for name in ("first.css", "second.css"):
        (tmp_path / name).write_text("body{}", "utf-8")
    event = threading.Event()
    calls = []

    def scanner(_text, url, *_args):
        calls.append(url)
        event.set()
        return set()

    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", scanner)
    monkeypatch.setattr(image_pipeline.os, "walk", lambda *_a, **_kw: iter([(str(tmp_path), [], ["first.css", "second.css"])]))
    with pytest.raises(DownloadCancelledError):
        scan.run()
    assert calls == [START + "first.css"]


def test_website_supplies_source_urls_and_only_successful_existing_cache_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(website, "_load_settings", lambda: {})
    downloader = website.WebsiteDownloader(START, str(tmp_path), archive_strategy="classic")
    monkeypatch.setattr(website, "legacy", lambda: SimpleNamespace(_DEEP_SCAN_ENABLED=True))
    css = tmp_path / "theme.css"
    css.write_text("body{}", "utf-8")
    missing = tmp_path / "missing.css"
    downloader._source_for_local[str(css)] = CDN + "main.css"
    downloader._downloaded.update({CDN + "main.css": str(css), CDN + "failed.css": None, CDN + "deleted.css": str(missing)})
    monkeypatch.setattr(downloader, "_download_html", lambda *_a: None)
    deep_scan = Mock(return_value={})
    monkeypatch.setattr(website, "_deep_scan_and_download_assets", deep_scan)
    try:
        downloader.download()
        arguments = deep_scan.call_args.kwargs
        assert arguments["source_urls"] == {str(css): CDN + "main.css"}
        assert arguments["skip_urls"] == {CDN + "main.css"}
    finally:
        downloader.close()
        downloader.session.close()


def test_css_import_is_not_reinterpreted_as_a_document_root_literal():
    assert image_pipeline._scan_file_for_assets(
        '@import "nested.css";', CDN + "main.css", START, ".css",
    ) == {CDN + "nested.css"}


@pytest.mark.parametrize("css,url,expected", [
    ('body{background:url(/shared/hero.png)}', CDN + "main.css", "https://cdn.example.test/shared/hero.png"),
    ('body{background:url(//images.example.test/hero.png)}', "http://cdn.example.test/theme/main.css", "http://images.example.test/hero.png"),
    ('body{background:url(./assets/hero.png)}', "https://cdn.example.test/assets/main.css", "https://cdn.example.test/assets/assets/hero.png"),
])
def test_css_resolution_uses_stylesheet_url_without_js_path_heuristics(css, url, expected):
    assert image_pipeline._scan_file_for_assets(css, url, START, ".css") == {expected}


@pytest.mark.skipif(os.environ.get("CYOA_RUNTIME_SMOKE") != "1", reason="optional live Chromium check")
def test_redirected_deep_stylesheet_renders_after_source_is_unavailable(scan, tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    game = tmp_path / "game"
    cdn = tmp_path / "cdn"
    game.mkdir()
    cdn.mkdir()
    (game / "index.html").write_text(
        '<!doctype html><html><head></head><body><h1>Offline story</h1><script src="main.abcdef12.js"></script></body></html>', "utf-8",
    )
    (game / "main.abcdef12.js").write_text(
        'const css=document.createElement("link");css.rel="stylesheet";css.href="redirect.css";document.head.appendChild(css);', "utf-8",
    )
    (cdn / "main.css").write_text('@import "nested.css";body{background-image:url(/cdn/pattern.png)}', "utf-8")
    (cdn / "nested.css").write_text('body{color:rgb(12,34,56)}', "utf-8")
    (cdn / "pattern.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII="
    ))
    offline = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.startswith(("/game/", "/cdn/")) and offline:
                self.send_error(503)
                return
            if self.path == "/game/redirect.css":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/cdn/main.css")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(website, "_load_settings", lambda: {})
    monkeypatch.setattr(website, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(website.legacy(), "_DEEP_SCAN_ENABLED", True)
    downloader = website.WebsiteDownloader(f"http://127.0.0.1:{server.server_port}/game/", str(tmp_path / "archive"), archive_strategy="classic")
    with requests.Session() as client:
        client.trust_env = False

        def fetch(url, **_kwargs):
            return client.get(url, timeout=5, stream=True)

        monkeypatch.setattr(downloader, "_fetch", fetch)
        monkeypatch.setattr(image_pipeline, "fetch_response", fetch)
        try:
            downloader.download()
            downloader.localize_existing_text_assets()
            assert downloader.validate_integrity()["missing"] == []
            offline.append(True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    online_requests = []
                    page.on("request", lambda request: online_requests.append(request.url) if "/cdn/" in request.url or "/game/" in request.url else None)
                    page.goto(f"http://127.0.0.1:{server.server_port}/archive/index.html", wait_until="networkidle")
                    assert page.locator("body").evaluate("node => getComputedStyle(node).color") == "rgb(12, 34, 56)"
                    assert page.evaluate("""async () => {
                        const url = getComputedStyle(document.body).backgroundImage.slice(5, -2);
                        const image = new Image(); image.src = url; await image.decode(); return image.complete;
                    }""")
                    assert online_requests == []
                finally:
                    browser.close()
        finally:
            downloader.close()
            downloader.session.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
