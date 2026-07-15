import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from cyoa_downloader_app.core.output import _cleanup_recent_part_files
from cyoa_downloader_app.core.url_utils import canonicalize_url
from cyoa_downloader_app.diagnostics import updates
from cyoa_downloader_app.download import image_pipeline
from cyoa_downloader_app.download import asset_scan, fonts
from cyoa_downloader_app.download.package import verify_output_package
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.importers.batch import _google_sheet_csv_export_url
from cyoa_downloader_app.integrations import ai_core
from cyoa_downloader_app.integrations.offline_viewers import registry
from cyoa_downloader_app.network import fetch_base
from cyoa_downloader_app.gui.app import CYOADownloaderGUI
from cyoa_downloader_app.importers import batch as batch_importer
from cyoa_downloader_app.project import cyoa_cafe, discover
from cyoa_downloader_app.project.cyoap_vue import BeautifulSoup
from cyoa_downloader_app.project.parse import (
    looks_like_project_payload,
    normalize_project_payload_text,
)
from cyoa_downloader_app.storage import cache as cache_store
from cyoa_downloader_app.storage import history as history_store


class FakeResponse:
    def __init__(self, status=200, headers=None, content=b"ok"):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.encoding = "utf-8"
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def close(self):
        self.closed = True

    def __bool__(self):
        return True


def test_google_sheet_url_conversion_handles_fragment_gid():
    url = "https://docs.google.com/spreadsheets/d/sheet_123/edit#gid=456"
    assert _google_sheet_csv_export_url(url).endswith("format=csv&gid=456")


def test_cyoap_vue_has_a_working_html_parser():
    soup = BeautifulSoup("<html><script src='dist/app.js'></script></html>", "html.parser")
    assert soup.find("script")["src"] == "dist/app.js"


def test_wrapped_app_project_payload_is_recognized_and_preserved():
    raw = json.dumps({"app": {"rows": [], "backpack": [], "title": "Wrapped", "image": "x.png"}})
    assert looks_like_project_payload(raw)
    normalized = normalize_project_payload_text(raw)
    assert json.loads(normalized)["app"]["title"] == "Wrapped"


def test_embedded_js_state_fragment_is_not_project_payload():
    fragment = "{linkedObjects:[],mainDiv:t.mainDiv,bCreatorMode:!1,isBackpack:a(),isOverDlg:!1,isOverImg:!1}"
    assert not looks_like_project_payload(fragment)


def test_local_viewer_name_alone_does_not_match_unrelated_html(monkeypatch):
    monkeypatch.setattr(registry, "_load_viewers_manifest", lambda: {
        "local": {"name": "LocalViewer", "viewer_type": "icc_plus"}
    })
    assert registry.get_viewer_for_site("<html><p>unrelated</p></html>", "embed") is None


def test_malformed_viewer_manifest_entries_are_ignored(tmp_path, monkeypatch):
    manifest = tmp_path / "viewers.json"
    manifest.write_text(json.dumps({
        "broken": "not-an-object",
        "usable": {"name": 42, "viewer_type": "icc_plus", "zip_filename": []},
    }), encoding="utf-8")
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))
    loaded = registry._load_viewers_manifest()
    assert "broken" not in loaded
    assert loaded["usable"]["name"] == "usable"
    assert loaded["usable"]["zip_filename"] == ""


def test_cleanup_removes_only_true_part_suffix(tmp_path):
    legitimate = tmp_path / "chapter.part.png"
    temporary = tmp_path / "chapter.png.1.2.part"
    legitimate.write_bytes(b"png")
    temporary.write_bytes(b"partial")
    assert _cleanup_recent_part_files(str(tmp_path), time.time() - 1) == 1
    assert legitimate.exists()
    assert not temporary.exists()


def test_malformed_manifest_entry_reports_failure_instead_of_crashing(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text(json.dumps({
        "files": {"asset.bin": "not-an-object"}, "file_count": 1,
    }), encoding="utf-8")
    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid manifest entry" in report


def test_invalid_manifest_json_is_not_treated_as_absent(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text("{broken", encoding="utf-8")
    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid or unreadable" in report


def test_ipv6_canonicalization_restores_brackets():
    assert canonicalize_url("http://[2001:db8::1]:8080/a") == "http://[2001:db8::1]:8080/a"


def test_entry_html_failure_is_fatal(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    monkeypatch.setattr(downloader, "_fetch", lambda _url: None)
    with pytest.raises(RuntimeError, match="entry HTML"):
        downloader.download()
    assert not (tmp_path / "index.html").exists()


def test_website_css_reuse_does_not_refetch_local_font(tmp_path, monkeypatch):
    css_dir = tmp_path / "css"
    fonts_dir = tmp_path / "fonts"
    css_dir.mkdir()
    fonts_dir.mkdir()
    (fonts_dir / "Roboto.woff2").write_bytes(b"font")
    css_path = css_dir / "main.css"
    css = '@font-face { font-family: Roboto; src: url("../fonts/Roboto.woff2"); }'
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("already-local font was fetched again")

    monkeypatch.setattr(downloader, "_download_asset", unexpected_fetch)
    assert downloader._process_css(
        css,
        "https://example.test/game/css/main.css",
        str(css_path),
    ) == css


def test_deep_scan_results_seed_website_cache_and_localize_without_refetch(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "pic.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"deep-scanned-image")
    css_path = tmp_path / "css" / "main.css"
    css_path.parent.mkdir()
    css_path.write_text(
        'body { background: url("https://example.test/game/images/pic.png"); }',
        encoding="utf-8",
    )
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    downloader._register_deep_scan_results({
        "https://example.test/game/images/pic.png": "images/pic.png",
    })

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("deep-scanned asset was fetched again")

    monkeypatch.setattr(downloader, "_fetch", unexpected_fetch)
    downloader.localize_existing_text_assets()

    assert "../images/pic.png" in css_path.read_text(encoding="utf-8")


def test_dns_resolved_internal_target_is_blocked_but_same_origin_local_is_allowed(monkeypatch):
    monkeypatch.setattr(ai_core.socket, "getaddrinfo", lambda *_a, **_k: [
        (2, 1, 6, "", ("127.0.0.1", 0)),
    ])
    ai_core._set_allow_internal_hosts(False)
    assert ai_core._ssrf_block_cross_origin("http://alias.test/secret", "https://public.test/game")
    assert not ai_core._ssrf_block_cross_origin("http://alias.test/a", "http://alias.test/b")
    ai_core._set_allow_internal_hosts(True)
    try:
        assert not ai_core._ssrf_block_cross_origin("http://alias.test/secret", "https://public.test/game")
    finally:
        ai_core._set_allow_internal_hosts(False)


def test_cyoa_cafe_candidate_guard_checks_dns_resolution(monkeypatch):
    monkeypatch.setattr(cyoa_cafe, "_host_resolves_internal", lambda host: host == "alias.test")
    resolver = cyoa_cafe.CYOACafeResolver(fetcher=lambda *_a, **_k: None)
    allowed, reason = resolver._candidate_allowed("https://alias.test/project.json")
    assert not allowed
    assert "internal" in reason


def test_cyoa_cafe_resolver_closes_probe_responses():
    response = FakeResponse(200, {"Content-Type": "text/html"}, b'<div id="app"></div>')
    resolver = cyoa_cafe.CYOACafeResolver(fetcher=lambda *_a, **_k: response)
    assert resolver.resolve("https://demo.cyoa.cafe/game/") == "https://demo.cyoa.cafe/game/"
    assert response.closed
    assert resolver._responses == {}


def test_remote_batch_import_closes_response(monkeypatch):
    response = FakeResponse(
        200,
        {"Content-Type": "text/csv"},
        b"url,filename\nhttps://example.test/game,story\n",
    )
    monkeypatch.setattr(batch_importer, "fetch_response", lambda *_a, **_k: response)
    assert batch_importer.import_queue_items_from_source("https://example.test/list.csv") == [{
        "url": "https://example.test/game",
        "filename": "story",
        "mode": "",
    }]
    assert response.closed


def test_discovered_project_urls_block_cross_origin_internal_hosts(monkeypatch):
    html = '<script>fetch("http://127.0.0.1:9000/project.json")</script>'
    assert discover.find_candidate_urls_in_text(html, "https://public.test/game/") == []
    local = discover.find_candidate_urls_in_text(
        '<script>fetch("project.json")</script>', "http://127.0.0.1:8000/game/",
    )
    assert local == ["http://127.0.0.1:8000/game/project.json"]

    calls = []
    monkeypatch.setattr(discover, "fetch_response", lambda *_a, **_k: calls.append(True))
    assert discover.try_project_candidate(
        "http://127.0.0.1:9000/project.json",
        source_url="https://public.test/game/",
    ) == (None, "")
    assert calls == []


def _prepare_fetch_base(monkeypatch, session):
    logger = SimpleNamespace(warning=lambda *_a, **_k: None,
                             error=lambda *_a, **_k: None,
                             info=lambda *_a, **_k: None,
                             debug=lambda *_a, **_k: None)
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(
        logger=logger, _CLOUDFLARE_MODE="off",
    ))
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **_k: session)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda _host: False)


def test_fetch_blocks_redirect_to_private_target(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse(302, {"Location": "http://127.0.0.1:9000/admin"})

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    assert fetch_base.base_fetch_response("https://public.test/start") is None
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


def test_fetch_keeps_verified_public_redirects_working(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                return FakeResponse(302, {"Location": "https://cdn.public.test/file"})
            return FakeResponse(200, {"Content-Type": "application/octet-stream"}, b"asset")

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    response = fetch_base.base_fetch_response(
        "https://public.test/start",
        as_bytes=True,
        extra_headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Trace": "kept",
        },
    )
    assert response.content == b"asset"
    assert [call[0] for call in session.calls] == [
        "https://public.test/start", "https://cdn.public.test/file",
    ]
    assert all(call[1]["verify"] is True for call in session.calls)
    redirected_headers = session.calls[1][1]["headers"]
    assert "Authorization" not in redirected_headers
    assert "Cookie" not in redirected_headers
    assert redirected_headers["X-Trace"] == "kept"


def test_fetch_blocks_redirect_to_non_http_scheme(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return FakeResponse(302, {"Location": "file:///etc/passwd"})

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    assert fetch_base.base_fetch_response("https://public.test/start") is None
    assert session.calls == ["https://public.test/start"]


def test_fetch_never_retries_with_tls_verification_disabled(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(kwargs)
            raise requests.exceptions.SSLError("bad certificate")

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    assert fetch_base.base_fetch_response("https://public.test/start") is None
    assert len(session.calls) == 1
    assert session.calls[0]["verify"] is True


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        ("flaresolverr_first", ["flaresolverr", "cloudscraper"]),
        ("cloudscraper_first", ["cloudscraper", "flaresolverr"]),
    ],
)
def test_cloudflare_auto_fallback_honors_priority(monkeypatch, priority, expected):
    class ChallengeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(bool(kwargs.get("headers")))
            return FakeResponse(
                403,
                {"Content-Type": "text/html", "Server": "cloudflare"},
                b"Checking your browser",
            )

    session = ChallengeSession()
    _prepare_fetch_base(monkeypatch, session)
    logger = SimpleNamespace(warning=lambda *_a, **_k: None,
                             error=lambda *_a, **_k: None,
                             info=lambda *_a, **_k: None,
                             debug=lambda *_a, **_k: None)
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(
        logger=logger, _CLOUDFLARE_MODE="auto", _CLOUDFLARE_PRIORITY=priority,
    ))
    calls = []

    def fake_flaresolverr(*_args, **_kwargs):
        calls.append("flaresolverr")
        return "CF_CHALLENGE"

    original_request = fetch_base._get_shared_session

    def request_with_backend_marker(*, use_cf=False):
        if use_cf:
            calls.append("cloudscraper")
        return session

    monkeypatch.setattr(fetch_base, "_get_shared_session", request_with_backend_marker)
    monkeypatch.setattr(fetch_base, "fetch_via_flaresolverr", fake_flaresolverr)
    assert fetch_base.base_fetch_response("https://protected.test/page") is None
    assert calls == expected


def test_queue_completion_removes_only_exact_duplicate_row():
    gui = CYOADownloaderGUI.__new__(CYOADownloaderGUI)
    gui._queue_data = [
        {"url": "https://same.test/game", "_queue_id": "first"},
        {"url": "https://same.test/game", "_queue_id": "second"},
        {"url": "https://new.test/game", "_queue_id": "new"},
    ]

    def remove_row(index):
        gui._queue_data.pop(index)

    gui._remove_row = remove_row
    assert gui._remove_queue_ids_from_queue({"first"}) == 1
    assert [item["_queue_id"] for item in gui._queue_data] == ["second", "new"]


def test_icc_project_image_pass_reuses_site_folder():
    source = Path(__file__).resolve().parents[1] / "cyoa_downloader_app" / "download" / "orchestrator.py"
    text = source.read_text(encoding="utf-8")
    icc_call = text[text.index("_, dl_result, _pi_urls = process_images("):]
    assert "site_folder=site_folder" in icc_call[:1200]


def test_process_images_reuses_existing_icc_image(monkeypatch, tmp_path):
    site = tmp_path / "icc"
    (site / "images").mkdir(parents=True)
    (site / "images" / "R1.avif").write_bytes(b"already downloaded")
    work = tmp_path / "work"
    raw = json.dumps({"rows": [{"objects": [{"image": "images/R1.avif"}]}]})

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("ICC image was fetched again instead of reused")

    monkeypatch.setattr(image_pipeline, "fetch_response", unexpected_fetch)
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://chuckeroo.cyoa.cafe/ucmccyoa/",
        download=True,
        temp_folder=str(work),
        site_folder=str(site),
        max_workers=1,
    )
    assert '"image":"images/R1.avif"' in downloaded
    assert not (work / "images").exists()


def test_process_images_reuses_existing_icc_audio(monkeypatch, tmp_path):
    site = tmp_path / "icc"
    (site / "audio").mkdir(parents=True)
    (site / "audio" / "click.mp3").write_bytes(b"already downloaded")
    work = tmp_path / "work"
    raw = json.dumps({"rows": [{"objects": [{"audio": "audio/click.mp3"}]}]})

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("ICC audio was fetched again instead of reused")

    monkeypatch.setattr(image_pipeline, "fetch_response", unexpected_fetch)
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://chuckeroo.cyoa.cafe/ucmccyoa/",
        download=True,
        temp_folder=str(work),
        site_folder=str(site),
        max_workers=1,
    )
    assert '"audio":"audio/click.mp3"' in downloaded
    assert not (work / "audio").exists()


def test_process_images_coalesces_relative_aliases(monkeypatch, tmp_path):
    calls = []
    response = FakeResponse(200, {"Content-Type": "image/png"}, b"same-image-bytes" * 8)

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return response

    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = json.dumps({"rows": [{"objects": [
        {"image": "./same.png"}, {"image": "same.png"},
    ]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    assert calls == ["https://example.test/game/same.png"]
    assert downloaded.count('"image":"images/same.png"') == 2
    assert not (tmp_path / "work" / "images" / "same_1.png").exists()


def test_process_images_flattens_external_image_paths_with_stable_names(monkeypatch, tmp_path):
    responses = {
        "https://cdn.example.test/original/05/8b/foo.jpg": FakeResponse(
            200, {"Content-Type": "image/jpeg"}, b"first-image" * 8
        ),
        "https://cdn.example.test/other/path/foo.jpg": FakeResponse(
            200, {"Content-Type": "image/jpeg"}, b"second-image" * 8
        ),
    }

    monkeypatch.setattr(image_pipeline, "fetch_response", lambda url, **_kwargs: responses[url])
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = json.dumps({"rows": [{"objects": [
        {"image": "https://cdn.example.test/original/05/8b/foo.jpg"},
        {"image": "https://cdn.example.test/other/path/foo.jpg"},
    ]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    image_files = list((tmp_path / "work" / "images").iterdir())
    assert len(image_files) == 2
    assert all(item.is_file() for item in image_files)
    assert all(item.name.startswith("cdn_example_test_foo_") for item in image_files)
    assert all("original" not in item.parts and "other" not in item.parts for item in image_files)
    assert downloaded.count('"image":"images/cdn_example_test_foo_') == 2


def test_image_content_dedup_is_scoped_per_output_folder(tmp_path):
    content = b"same-content"
    first_folder = tmp_path / "first" / "images"
    second_folder = tmp_path / "second" / "images"
    first_folder.mkdir(parents=True)
    second_folder.mkdir(parents=True)
    first_path = str(first_folder / "a.png")

    assert asset_scan._check_image_dedup(content, first_path, scope=str(first_folder)) is None
    assert asset_scan._check_image_dedup(
        content, str(first_folder / "b.png"), scope=str(first_folder),
    ) == first_path
    assert asset_scan._check_image_dedup(
        content, str(second_folder / "a.png"), scope=str(second_folder),
    ) is None


def test_font_aliases_fetch_once_and_same_name_different_bytes_are_preserved(monkeypatch, tmp_path):
    calls = []
    payloads = {
        "https://cdn.test/font.woff2?v=1": b"font-one",
        "https://other.test/font.woff2": b"font-two",
    }

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return FakeResponse(200, {"Content-Type": "font/woff2"}, payloads[url])

    monkeypatch.setattr(fonts, "fetch_response", fake_fetch)
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "font.woff2").write_bytes(b"pre-existing-font")
    project = json.dumps({
        "a": "https://cdn.test/font.woff2?v=1",
        "b": "https://cdn.test/font.woff2?v=2",
        "c": "https://other.test/font.woff2",
    })

    rewritten = fonts._download_fonts_into_folder(
        project, "https://example.test/game/", str(tmp_path),
    )

    assert sorted(calls) == [
        "https://cdn.test/font.woff2?v=1",
        "https://other.test/font.woff2",
    ]
    assert (fonts_dir / "font.woff2").read_bytes() == b"pre-existing-font"
    assert (fonts_dir / "font_1.woff2").read_bytes() == b"font-one"
    assert (fonts_dir / "font_2.woff2").read_bytes() == b"font-two"
    assert rewritten.count("fonts/font_1.woff2") == 2
    assert rewritten.count("fonts/font_2.woff2") == 1


def test_deep_scan_coalesces_cachebusters_but_keeps_query_variants(monkeypatch, tmp_path):
    (tmp_path / ".vite").mkdir()
    (tmp_path / "build").mkdir()
    for manifest in (".vite/manifest.json", "asset-manifest.json", "manifest.json", "build/asset-manifest.json"):
        path = tmp_path / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")

    candidates = {
        "https://example.test/game/foo.png?v=1",
        "https://example.test/game/foo.png?v=2",
        "https://example.test/game/foo.png?width=1",
        "https://example.test/game/foo.png?width=2",
    }
    calls = []

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return FakeResponse(200, {"Content-Type": "image/png"}, url.encode() * 2)

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_get_active_proxy", lambda: "")
    monkeypatch.setattr(image_pipeline, "_legacy", lambda: SimpleNamespace(_HTTP2_ENABLED=False))
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = image_pipeline._deep_scan_and_download_assets(
        str(tmp_path), "https://example.test/game/", str(tmp_path),
        max_workers=3, ai_mode="off",
    )

    assert calls.count("https://example.test/game/foo.png") == 1
    assert len(downloaded) == 3
    assert len(set(downloaded.values())) == 3
    assert all((tmp_path / rel).is_file() for rel in downloaded.values())


def test_process_images_rejects_html_200_as_image(monkeypatch, tmp_path):
    response = FakeResponse(200, {"Content-Type": "text/html"}, b"<html>login page</html>")
    monkeypatch.setattr(image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)
    raw = json.dumps({"rows": [{"objects": [{"image": "https://cdn.test/pic.png"}]}]})
    embedded, _downloaded, _failed = image_pipeline.process_images(
        raw, "https://public.test/game/", embed=True, output_dir=str(tmp_path), max_workers=1,
    )
    assert "data:text/html" not in embedded
    assert "https://cdn.test/pic.png" in embedded


def test_corrupt_cache_index_entries_become_cache_misses(tmp_path, monkeypatch):
    index = tmp_path / "index.json"
    index.write_text(json.dumps({
        "https://bad.test/a.png": 42,
        "https://bad.test/b.png": "not-a-sha256",
    }), encoding="utf-8")
    monkeypatch.setattr(cache_store, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache_store, "_CACHE_IDX", index)
    monkeypatch.setattr(cache_store, "_cache_index", {})
    monkeypatch.setattr(cache_store, "_cache_loaded", False)
    assert cache_store._cache_get("https://bad.test/a.png") is None
    assert cache_store._cache_get("https://bad.test/b.png") is None
    assert cache_store._cache_index == {}


def test_malformed_history_entries_are_filtered(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "bad": "entry",
        "https://good.test": {"last_downloaded": "2026-01-01T00:00:00"},
    }), encoding="utf-8")
    monkeypatch.setattr(history_store, "_HISTORY_FILE", str(path))
    assert history_store._load_history() == {
        "https://good.test": {"last_downloaded": "2026-01-01T00:00:00"}
    }


def test_batch_update_probe_closes_error_responses_and_skips_bad_history(monkeypatch):
    response = FakeResponse(404, {"Content-Length": "0"}, b"")
    monkeypatch.setattr(updates, "fetch_response", lambda *_a, **_k: response)
    results = updates._batch_check_updates({
        "bad": "entry",
        "https://good.test": {"success": True, "filename": "Good"},
    }, max_workers=0)
    assert results == [{
        "url": "https://good.test",
        "name": "Good",
        "status": "unreachable",
        "reason": "HTTP 404",
    }]
    assert response.closed


@pytest.mark.parametrize("module_name", [
    "cyoa_downloader_app.download.image_pipeline",
    "cyoa_downloader_app.download.orchestrator",
    "cyoa_downloader_app.gui.panels",
])
def test_domain_modules_import_in_fresh_interpreter(module_name):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
