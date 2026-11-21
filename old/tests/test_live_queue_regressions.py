"""Regressions discovered while archiving the real user queue."""

import functools
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urljoin

import pytest
import requests

from cyoa_downloader_app import cli
from cyoa_downloader_app.download import image_pipeline, perchance
from cyoa_downloader_app.download.archive_profiler import profile_archive_target
from cyoa_downloader_app.download.asset_scan import (
    _deep_scan_project_assets,
    _extract_image_references,
    _scan_file_for_assets,
    _scan_large_json_for_assets,
)
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.integrations.offline_viewers.modernizer import _compact_project
from cyoa_downloader_app.network import browser as browser_module
from cyoa_downloader_app.network.browser import _browser_session_state, _make_cookie_session
from cyoa_downloader_app.network.runtime_capture import (
    _is_runtime_asset_response,
    _is_safe_interaction_label,
    capture_runtime_assets,
)
from tools import run_live_queue


def test_hjson_data_files_are_discovered_without_vite_virtual_module_ids():
    assets = _scan_file_for_assets(
        'nt.fetchHjson("cards/artists.hjson");'
        'nt.fetchHjson("patch-notes/content.hjson");'
        'const modules={"/src/assets/dialogue.hjson":()=>import("./dialogue-hash.js")};',
        "https://example.test/game/assets/app.js",
        "https://example.test/game/",
    )
    assert "https://example.test/game/cards/artists.hjson" in assets
    assert "https://example.test/game/patch-notes/content.hjson" in assets
    assert not any("/src/assets/" in url for url in assets)
    assert _is_runtime_asset_response("https://example.test/game/cards/artists.hjson", "text/plain")


def test_tiled_pathway_viewer_enumerates_currency_and_pathway_icons():
    data = {
        "meta": {
            "pages": 1,
            "pageSize": [1600, 1000],
            "currencies": [{"key": "W"}],
            "pathways": [{"key": "fool"}, {"key": "../unsafe"}],
        },
        "sections": [],
        "options": {},
    }
    assets = _scan_file_for_assets(
        "window.CYOA = " + json.dumps(data) + ";", "https://example.test/cyoa.js", "https://example.test/"
    )
    assert {
        "https://example.test/ui/W.png",
        "https://example.test/ui/p-fool.png",
        "https://example.test/ui/p-all.png",
    } <= assets
    assert not any("unsafe" in url for url in assets)


def test_missing_hjson_does_not_crawl_the_hosts_html_fallback(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path), ai_mode="off")
    fetched = []

    def fetch(url):
        fetched.append(url)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "text/html"
        response._content = b'<!doctype html><html><script src="app.js"></script></html>'
        response._content_consumed = True
        return response

    monkeypatch.setattr(downloader, "_fetch", fetch)
    try:
        assert downloader.download_asset("https://example.test/game/missing.hjson") is None
        assert fetched == ["https://example.test/game/missing.hjson"]
    finally:
        downloader.close()


def test_bundled_media_urls_resolve_from_both_document_and_module(tmp_path):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path), ai_mode="off")
    asset = tmp_path / "assets/banner.webp"
    asset.parent.mkdir()
    asset.write_bytes(b"image")
    remote = "https://cdn.example/banner.webp"
    downloader._downloaded[remote] = str(asset)
    try:
        rewritten = downloader._process_js(
            'const banner="' + remote + '";',
            "https://example.test/game/assets/index-abcdefgh.js",
            str(tmp_path / "assets/index-abcdefgh.js"),
        )
        reference = rewritten.split('"')[1]
        expected = "http://localhost/assets/banner.webp"
        assert urljoin("http://localhost/index.html", reference) == expected
        assert urljoin("http://localhost/assets/index-abcdefgh.js", reference) == expected
    finally:
        downloader.close()


def test_tiled_overlay_viewer_enumerates_hidden_pages_without_browser():
    data = {
        "meta": {"pages": 2},
        "sections": [],
        "options": [],
        "pages": {
            "1": {"w": 1600, "h": 2500, "tile": 1000, "tiles": 3},
            "2": {
                "w": 1600,
                "h": 1000,
                "tile": 1000,
                "tiles": 1,
                "anim": {"src": "pages/p02/anim.webp", "still": "pages/p02/still.webp"},
            },
        },
    }
    assets = _scan_file_for_assets(
        "window.CYOA = " + json.dumps(data) + ";", "https://example.test/game/cyoa.js", "https://example.test/game/"
    )
    expected = {
        "pages/p01/t0.webp",
        "pages/p01/t1.webp",
        "pages/p01/t2.webp",
        "pages/p01/preview.webp",
        "pages/p02/preview.webp",
        "pages/p02/anim.webp",
        "pages/p02/still.webp",
    }
    assert {"https://example.test/game/" + path for path in expected} <= assets
    assert "https://example.test/game/pages/p02/t0.webp" not in assets


def test_cli_does_not_enumerate_interfaces_for_disabled_vpn_guard(monkeypatch):
    monkeypatch.setattr(cli.runtime_state, "_vpn_policy", "system")

    def unexpected():
        raise AssertionError("Interface enumeration should be skipped")

    monkeypatch.setattr(cli, "get_vpn_status", unexpected)
    assert cli._cli_vpn_status()["policy"] == "system"
    monkeypatch.setattr(cli.runtime_state, "_vpn_policy", "require")
    monkeypatch.setattr(cli, "get_vpn_status", lambda: {"policy": "require", "available": False})
    assert cli._cli_vpn_status()["available"] is False


def test_mediawiki_image_field_downloads_linked_original_without_headless(tmp_path, monkeypatch):
    page = "https://wiki.example.test/wiki/File:Picture.png"
    image = "https://cdn.example.test/original.png"
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "text/html" if url == page else "image/png"
        response._content = (
            f'<div class="fullImageLink"><a href="{image}">Original file</a></div>'.encode()
            if url == page
            else b"\x89PNG\r\n\x1a\n" + b"0" * 256
        )
        response._content_consumed = True
        return response

    def headless(*args, **kwargs):
        raise AssertionError("No browser is needed for an explicit original-file link")

    monkeypatch.setattr(image_pipeline, "_cache_get", lambda url: None)
    monkeypatch.setattr(image_pipeline, "_cache_get_file", lambda *a, **kw: None)
    monkeypatch.setattr(image_pipeline, "_cache_put_file", lambda *a, **kw: None)
    monkeypatch.setattr(image_pipeline, "fetch_response", fetch)
    monkeypatch.setattr(image_pipeline, "_fetch_headless", headless)
    text = json.dumps({"rows": [{"objects": [{"image": page}]}]})
    _embedded, localized, _urls = image_pipeline.process_images(
        text, "https://example.test/", download=True, temp_folder=str(tmp_path), max_workers=1
    )
    assert calls == [page, image]
    assert json.loads(localized)["rows"][0]["objects"][0]["image"].startswith("images/")


def test_image_credit_fused_to_malformed_url_keeps_filename_apostrophe():
    value = "Artist credit CC BY-SA 4.0https:/cdn.example.test/Mam'etu.jpg"
    assert _extract_image_references(value, allow_bare_path=True) == {"https://cdn.example.test/Mam'etu.jpg"}


def test_source_tooltip_links_are_preserved_without_becoming_image_or_audio_jobs():
    credits = "Art CC BY-SA 4.0https:/commons.wikimedia.org/wiki/File:Mam'etu.jpg https://youtu.be/abcdefghijk"
    project = {
        "rows": [
            {
                "objects": [
                    {
                        "image": "images/actual.png",
                        "imageSourceTooltip": credits,
                        "backgroundImageSourceTooltip": '<img src="images/license-badge.svg">',
                    }
                ]
            }
        ]
    }
    images, audio, youtube = _deep_scan_project_assets(json.dumps(project), "https://example.test/")
    assert images == {"images/actual.png", "images/license-badge.svg"}
    assert not audio and not youtube
    assert project["rows"][0]["objects"][0]["imageSourceTooltip"] == credits
    assert _scan_large_json_for_assets(json.dumps(project), "https://example.test/project.json") == {
        "https://example.test/images/actual.png",
        "https://example.test/images/license-badge.svg",
    }


def test_successful_plain_credit_image_field_becomes_a_local_path(tmp_path, monkeypatch):
    url = "https://cdn.example.test/Mam'etu.jpg"
    credit = "Artist credit CC BY-SA 4.0https:/cdn.example.test/Mam'etu.jpg"
    calls = []

    def fetch(actual, **kwargs):
        calls.append(actual)
        assert actual == url
        response = requests.Response()
        response.status_code = 200
        response.url = actual
        response.headers["Content-Type"] = "image/png"
        response._content = b"\x89PNG\r\n\x1a\n" + b"0" * 256
        response._content_consumed = True
        return response

    monkeypatch.setattr(image_pipeline, "fetch_response", fetch)
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda url: None)
    monkeypatch.setattr(image_pipeline, "_cache_get_file", lambda *a, **kw: None)
    monkeypatch.setattr(image_pipeline, "_cache_put_file", lambda *a, **kw: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *args: None)
    raw = json.dumps({"rows": [{"objects": [{"image": credit, "text": credit}]}]})
    _embedded, localized, _refs = image_pipeline.process_images(
        raw, "https://example.test/", download=True, temp_folder=str(tmp_path), max_workers=1
    )
    choice = json.loads(localized)["rows"][0]["objects"][0]
    assert calls == [url]
    assert choice["image"].startswith("images/")
    assert choice["text"].startswith("Artist credit CC BY-SA 4.0")


def test_unresolved_javascript_templates_are_not_download_urls():
    assets = _scan_file_for_assets(
        "const bg = `background-image:url(pages/p${pn}/preview.webp)`;",
        "https://example.test/game/app.js",
        "https://example.test/game/",
    )
    assert not any("${" in url for url in assets)


def test_tiled_viewer_uses_authored_shared_page_dimensions():
    source = "window.CYOA = " + json.dumps(
        {"meta": {"pages": 2, "pageSize": [1600, 2100]}, "sections": [], "options": {}}
    )
    assets = _scan_file_for_assets(source, "https://example.test/cyoa.js", "https://example.test/")
    assert {f"https://example.test/pages/p{p:02d}/t{k}.webp" for p in (1, 2) for k in range(3)} <= assets
    assert "https://example.test/pages/p02/preview.webp" in assets


def test_conditional_document_write_preserves_both_language_data_scripts():
    source = (
        """document.write('<script src="data/story' + (lang === 'en' ? '.en' : '') + '.js?v=' + v + '"><\\/script>');"""
    )
    assets = _scan_file_for_assets(source, "https://example.test/game/js/i18n.js", "https://example.test/game/")
    assert "https://example.test/game/data/story.js" in assets
    assert "https://example.test/game/data/story.en.js" in assets


def test_auto_detects_inline_application_with_staged_controls(tmp_path):
    entry = tmp_path / "index.html"
    entry.write_text(
        """<button>Begin your adventure</button><script>
    document.body.innerHTML='<button>Continue</button>';
    document.addEventListener('click',()=>{});</script>""",
        encoding="utf-8",
    )
    result = profile_archive_target(
        SimpleNamespace(start_url="https://example.test/", start_html_local=str(entry), output_folder=str(tmp_path))
    )
    assert result.effective_strategy == "browser"


@pytest.mark.parametrize("label", ["Begin your adventure", "Start game", "Open all", "Confirm"])
def test_read_only_opening_controls_are_allowed(label):
    assert _is_safe_interaction_label(label)


@pytest.mark.parametrize("label", ["Start over", "Restart game", "Clear choices", "Confirm purchase"])
def test_opening_allowlist_does_not_allow_reset_or_purchase(label):
    assert not _is_safe_interaction_label(label)


def test_runtime_browser_only_receives_matching_unexpired_session_cookies():
    session = requests.Session()
    session.headers["User-Agent"] = "Solved browser agent"
    session.cookies.set("clearance", "test", domain=".example.test", path="/", secure=True)
    session.cookies.set("other", "private", domain="other.test", path="/")
    session.cookies.set("expired", "old", domain="example.test", path="/", expires=1)
    agent, cookies = _browser_session_state(["https://story.example.test/"], session)
    assert agent == "Solved browser agent"
    assert [cookie["name"] for cookie in cookies] == ["clearance"]


def test_cookie_decryption_failure_does_not_abort_download(monkeypatch):
    import sys

    class CookieError(Exception):
        pass

    def broken_loader():
        raise CookieError("Unable to get key for cookie decryption")

    module = SimpleNamespace(
        **{name: broken_loader for name in ("chrome", "chromium", "firefox", "edge", "brave", "opera", "safari")},
        BrowserCookieError=CookieError,
    )
    monkeypatch.setitem(sys.modules, "browser_cookie3", module)
    assert _make_cookie_session("firefox") is None


def test_older_plus2_projects_receive_missing_runtime_collections():
    source = {"version": "2.10.8", "rows": [], "variables": [{"id": "authored"}]}
    result = json.loads(_compact_project(json.dumps(source)))
    assert result["backpack"] == []
    assert result["customFonts"] == []
    assert result["variables"] == source["variables"]
    assert "backpack" not in source


def test_project_first_archive_localizes_custom_css_imports(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    local = tmp_path / "style" / "theme.css"
    local.parent.mkdir()
    calls = []

    def download(url, **kwargs):
        calls.append(url)
        local.write_text("body{color:blue}", encoding="utf-8")
        return str(local)

    monkeypatch.setattr(downloader, "_download_asset", download)
    monkeypatch.setattr(downloader, "localize_project_google_fonts", lambda text: 0)
    try:
        downloader.write_project_payload(
            "https://example.test/data/project.json",
            json.dumps({"rows": [], "customCSS": '@import url("style/theme.css?v=abc");'}),
        )
    finally:
        downloader.close()
        downloader.session.close()
    assert calls == ["https://example.test/game/style/theme.css?v=abc"]
    saved = json.loads((tmp_path / "project.json").read_text("utf-8"))
    assert "style/theme.css" in saved["customCSS"]
    assert "?v=abc" not in saved["customCSS"]


def test_project_images_delegate_rate_limiting_once_to_http_transport(tmp_path, monkeypatch):
    throttled = []

    def throttle(url):
        throttled.append(url)

    def fetch(url, **kwargs):
        # The HTTP transport owns throttling, including retries and backoff.
        throttle(url)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "image/png"
        response._content = b"\x89PNG\r\n\x1a\n" + b"0" * 256
        response._content_consumed = True
        return response

    monkeypatch.setattr(image_pipeline, "_cache_get", lambda url: None)
    monkeypatch.setattr(image_pipeline, "_cache_get_file", lambda *a, **kw: None)
    monkeypatch.setattr(image_pipeline, "_cache_put_file", lambda *a, **kw: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", throttle)
    monkeypatch.setattr(image_pipeline, "fetch_response", fetch)
    text = json.dumps({"rows": [{"objects": [{"image": "https://example.test/rate-limit.png"}]}]})
    image_pipeline.process_images(
        text, "https://example.test/", download=True, temp_folder=str(tmp_path), max_workers=1
    )
    assert throttled == ["https://example.test/rate-limit.png"]


def test_perchance_adapter_uses_public_versioned_get_data(monkeypatch):
    urls = []
    source = {
        "generator": {
            "outputTemplate": "<button>Start game</button>",
            "srcManifest": {"app.js": {"key": "a" * 32 + ".js"}},
            "imports": [],
        }
    }

    def read(url):
        urls.append(url)
        return (
            {"status": "success", "lastEditTime": 123, "saveCount": 4, "editorPayloadRevision": 5, "schema": 2}
            if len(urls) == 1
            else source
        )

    monkeypatch.setattr(perchance, "_read_public_json", read)
    assert perchance.fetch_perchance_source("https://perchance.org/public-story") == source
    assert urls[-1].endswith("/public-story/123-4-5-2")
    assert perchance.fetch_perchance_source("https://other.test/public-story") is None
    assert len(urls) == 2


def test_perchance_expression_templates_keep_normal_engine_fallback():
    assert not perchance._standalone_template(
        {"outputTemplate": "<main>[output]</main>", "srcManifest": {"app.js": {}}}
    )


def test_perchance_source_manifest_archives_all_files_and_localizes_image_map(tmp_path, monkeypatch):
    image = "https://user.uploads.dev/file/" + "b" * 32 + ".svg"
    source = {
        "generator": {
            "outputTemplate": '<script src="src/data.js"></script><img src="' + image + '">',
            "srcManifest": {"data.js": {"key": "a" * 32 + ".js"}, "images/card.svg": {"key": "b" * 32 + ".svg"}},
        }
    }

    def fetch(url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = (
            ('window.image="' + image + '";').encode()
            if url.endswith(".js")
            else b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(perchance, "fetch_response", fetch)
    manifest = perchance.archive_perchance_source(source, "https://perchance.org/public-story", str(tmp_path))
    assert manifest["failed_assets"] == []
    assert (tmp_path / "src/images/card.svg").is_file()
    assert "src/images/card.svg" in (tmp_path / "src/data.js").read_text("utf-8")
    assert "src/images/card.svg" in (tmp_path / "index.html").read_text("utf-8")
    assert (tmp_path / "perchance_source.json").is_file()


@pytest.mark.skipif(os.environ.get("CYOA_RUNTIME_SMOKE") != "1", reason="requires real Chromium")
def test_four_staged_gates_capture_assets_and_block_delayed_post(tmp_path, caplog):
    caplog.set_level("DEBUG", logger="cyoa_downloader")
    (tmp_path / "index.html").write_text(
        """<button id="gate">Begin your adventure</button>
    <script>let stage=0; gate.onclick=()=>{
      if(++stage<4){gate.textContent=['','Continue','Confirm','Start game'][stage]; return;}
      gate.remove(); fetch('payload.json');
      setTimeout(()=>fetch('mutation',{method:'POST',body:'no'}),1000);
    };</script>""",
        encoding="utf-8",
    )
    (tmp_path / "payload.json").write_text('{"loaded":true}', encoding="utf-8")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sink = SimpleNamespace(_kind_from=lambda *a, **k: "json", download_asset=lambda *a, **k: "saved")
    try:
        result = capture_runtime_assets(
            sink,
            [f"http://127.0.0.1:{server.server_port}/index.html"],
            settle_time_ms=1200,
            capture_interactions=True,
            max_scroll_steps=1,
            max_interactions=8,
            no_progress_rounds=1,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert result.interactions_attempted == 4
    assert any(url.endswith("/payload.json") for url in result.downloaded)
    assert any(item["method"] == "POST" for item in result.blocked_requests)


def test_parallel_checkpoint_updates_do_not_share_a_temporary_file(tmp_path):
    target = tmp_path / "status.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda number: run_live_queue.write_json(target, {"number": number}), range(32)))
    assert json.loads(target.read_text("utf-8"))["number"] in range(32)
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("failure", ["get", "quit"])
def test_selenium_transport_timeout_does_not_abort_image_job(monkeypatch, failure):
    import base64

    from urllib3.exceptions import ReadTimeoutError

    playwright = pytest.importorskip("playwright.sync_api")
    webdriver = pytest.importorskip("selenium.webdriver")
    content = b"\x89PNG\r\n\x1a\n" + b"0" * 256
    calls = []

    class Driver:
        def set_page_load_timeout(self, value):
            pass

        def set_script_timeout(self, value):
            pass

        def get(self, url):
            if failure == "get":
                raise ReadTimeoutError(None, url, "driver timeout")

        def execute_async_script(self, *args):
            return {"ok": True, "data": base64.b64encode(content).decode(), "contentType": "image/png"}

        def quit(self):
            calls.append("quit")
            if failure == "quit":
                raise ReadTimeoutError(None, "localhost", "quit timeout")

    def no_playwright():
        raise RuntimeError("Playwright unavailable")

    monkeypatch.setattr(playwright, "sync_playwright", no_playwright)
    monkeypatch.setattr(webdriver, "Chrome", lambda **kwargs: Driver())
    monkeypatch.setattr(browser_module, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(browser_module, "_get_browser_proxy_config", lambda url: {})
    result = browser_module._fetch_headless("https://example.test/image.png", reject_error_documents=True)
    assert result == (None if failure == "get" else content)
    assert calls == ["quit"]


def test_resumed_job_selects_latest_archive_entry(tmp_path):
    audit = tmp_path / "_audit"
    audit.mkdir()
    for folder, stamp in [("archive", 1), ("archive_1", 2)]:
        entry = tmp_path / "Story" / folder / "index.html"
        entry.parent.mkdir(parents=True)
        entry.write_text(folder, encoding="utf-8")
        os.utime(entry, (stamp, stamp))
    result = run_live_queue.finish_job(
        {"id": "test", "folder": "Story", "status": "downloaded", "started": time.time()}, tmp_path, audit
    )
    assert result["entry"] == "Story/archive_1/index.html"
