from __future__ import annotations

import io
import pathlib
from urllib.parse import quote

import requests
import cyoa_downloader_app.download.website as website_module

from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download.archive_profiler import (
    profile_archive_target, project_archive_profile,
)
from cyoa_downloader_app.download.archive_runner import run_archive_extensions
from cyoa_downloader_app.download.cyoa_cafe_static import download_cyoa_cafe_static_record
from cyoa_downloader_app.download.package import verify_output_package
from cyoa_downloader_app.download.route_crawler import RouteCrawler
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.download.asset_scan import _infer_dynamic_asset_paths, _scan_file_for_assets
from cyoa_downloader_app.network.runtime_capture import (
    _is_runtime_asset_response, _is_safe_interaction_label,
)
from cyoa_downloader_app.project.cyoa_cafe import (
    build_cyoa_cafe_file_url, classify_cyoa_cafe_record,
)
from cyoa_downloader_app.cli import _safe_console_print


def _bare_downloader(tmp_path: pathlib.Path) -> WebsiteDownloader:
    downloader = WebsiteDownloader.__new__(WebsiteDownloader)
    downloader.start_url = "https://example.test/game/story"
    downloader.output_folder = str(tmp_path)
    downloader.start_html_local = str(tmp_path / "index.html")
    downloader._used_local_paths = set()
    downloader._collision_log = []
    return downloader


def test_cache_key_strips_only_cache_busters(tmp_path):
    downloader = _bare_downloader(tmp_path)

    assert downloader._normalize_cache_key("https://x.test/app.js?v=one") == "https://x.test/app.js"
    assert downloader._normalize_cache_key("https://x.test/app.js?v=two") == "https://x.test/app.js"
    assert downloader._normalize_cache_key("https://x.test/app.js?dpl=deploy-id") == "https://x.test/app.js"
    assert downloader._normalize_cache_key("https://x.test/image?w=320") != downloader._normalize_cache_key(
        "https://x.test/image?w=1280"
    )


def test_cli_report_is_safe_on_legacy_windows_encoding():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    _safe_console_print("PASS ✓ / FAIL ✗", file=stream)
    stream.flush()
    assert b"PASS ? / FAIL ?" in raw.getvalue()


def test_package_verifier_ignores_minified_js_and_source_map_false_positives(tmp_path):
    (tmp_path / "index.html").write_text(
        '<script src="app.js"></script><link rel="stylesheet" href="app.css">',
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        'i.src=e.src;n||(i.style.cssText="left:0");'
        'e.download="canvas.png";//# sourceMappingURL=app.js.map',
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(
        'body{color:#000}/*# sourceMappingURL=materialdesignicons.css.map */',
        encoding="utf-8",
    )

    ok, report = verify_output_package(str(tmp_path))

    assert ok, report
    assert "canvas.png" not in report
    assert "materialdesignicons.css" not in report


def test_failed_relative_html_asset_becomes_explicit_online_fallback(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    downloader.start_url = "https://example.test/story/"
    downloader._downloaded = {}
    tag = {"href": "font/missing.css"}
    monkeypatch.setattr(downloader, "_download_asset", lambda *args, **kwargs: None)

    downloader._set_attr_local(
        tag, "href", downloader.start_url, str(tmp_path / "index.html"),
        preferred_kind="css",
    )

    assert tag["href"] == "https://example.test/story/font/missing.css"


def test_next_image_proxy_is_unwrapped_to_original_asset(tmp_path):
    downloader = _bare_downloader(tmp_path)
    original = "https://cdn.sanity.io/images/demo/photo.jpg"
    proxy = "/_next/image?url=" + quote(original, safe="") + "&w=1200&q=75"

    assert downloader._normalize_remote_url(proxy, downloader.start_url) == original
    assert downloader._normalize_remote_url("http://[not-an-ipv6/image.png", downloader.start_url) is None
    assert not downloader._should_download_from_text("http://[not-an-ipv6/image.png")


def test_meaningful_query_gets_stable_distinct_local_name(tmp_path):
    downloader = _bare_downloader(tmp_path)

    first = downloader._allocate_local_path(
        "https://example.test/game/story/image?id=one", content_type="image/jpeg"
    )
    second = downloader._allocate_local_path(
        "https://example.test/game/story/image?id=two", content_type="image/jpeg"
    )

    assert first != second
    assert first.endswith(".jpg")
    assert second.endswith(".jpg")


class _FakeDownloader:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.start_url = "https://example.test/game/story"
        self.output_folder = str(tmp_path)
        self.start_html_local = str(tmp_path / "index.html")
        self._html = {
            "https://example.test/game/story": (
                '<a href="/game/story/choice?from=story">Choose</a>'
                '<a href="/login">Login</a><a href="https://outside.test/x">Outside</a>'
            ),
            "https://example.test/game/story/choice": '<h1 id="choice">Choice</h1>',
        }

    def _fetch(self, url: str):
        text = self._html.get(url)
        if text is None:
            return None
        response = requests.Response()
        response.status_code = 200
        response._content = text.encode("utf-8")
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.url = url
        return response

    def download_html_page(self, url: str, local_html: str, html_text: str) -> None:
        path = pathlib.Path(local_html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_text, encoding="utf-8")


def test_route_crawler_stays_in_story_scope_and_rewrites_links(tmp_path):
    downloader = _FakeDownloader(tmp_path)
    result = RouteCrawler(
        downloader,
        ArchivePolicy(strategy="smart", max_pages=10, max_depth=5),
    ).crawl()

    assert set(result.pages) == {
        "https://example.test/game/story",
        "https://example.test/game/story/choice",
    }
    root = pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8")
    assert "routes/choice/index.html" in root
    assert "data-cyoa-local-route" in root
    assert "/login" in root


def test_route_crawler_reports_limit_and_preserves_zero_depth(tmp_path):
    limited = RouteCrawler(
        _FakeDownloader(tmp_path / "limited"),
        ArchivePolicy(strategy="smart", max_pages=1, max_depth=5),
    ).crawl()
    assert limited.limit_reached is True
    assert limited.remaining_queued == 1
    assert len(limited.pages) == 1

    shallow = RouteCrawler(
        _FakeDownloader(tmp_path / "shallow"),
        ArchivePolicy(strategy="smart", max_pages=10, max_depth=0),
    ).crawl()
    assert len(shallow.pages) == 1
    assert shallow.limit_reached is False


def test_route_local_names_are_windows_safe_and_collision_resistant(tmp_path):
    crawler = RouteCrawler(
        _FakeDownloader(tmp_path), ArchivePolicy(strategy="smart"),
    )
    reserved = pathlib.Path(crawler._route_local_path("https://example.test/game/story/CON"))
    first = crawler._route_local_path("https://example.test/game/story/a%3Ab")
    second = crawler._route_local_path("https://example.test/game/story/a_b")

    assert reserved.parent.name == "_CON"
    assert first != second


def test_route_crawler_ignores_malformed_ipv6_links(tmp_path):
    crawler = RouteCrawler(
        _FakeDownloader(tmp_path), ArchivePolicy(strategy="smart"),
    )
    links = crawler._links_from(
        '<a href="http://[broken-ipv6/path">bad</a><a href="choice">good</a>',
        "https://example.test/game/story/",
    )
    assert links == ["https://example.test/game/story/choice"]


def test_classic_policy_does_not_enable_extra_stages():
    policy = ArchivePolicy().normalized()
    assert policy.strategy == "classic"
    assert not policy.crawl_routes
    assert not policy.capture_runtime
    assert policy.runtime_max_pages == 12


def test_archive_policy_normalizes_malformed_programmatic_values():
    policy = ArchivePolicy(
        strategy="UNKNOWN", max_pages="bad", max_depth=0,
        settle_time_ms=float("inf"), runtime_max_pages=None,
    ).normalized()

    assert policy.strategy == "classic"
    assert policy.max_pages == 300
    assert policy.max_depth == 0
    assert policy.settle_time_ms == 1800
    assert policy.runtime_max_pages == 12


def test_auto_archive_policy_and_safe_runtime_limits_are_bounded():
    policy = ArchivePolicy(
        strategy="AUTO", interaction_policy="SAFE", max_scroll_steps=99999,
        max_interactions=-4, no_progress_rounds=0,
    ).normalized()

    assert policy.strategy == "auto"
    assert policy.interaction_policy == "safe"
    assert policy.max_scroll_steps == 1000
    assert policy.max_interactions == 0
    assert policy.no_progress_rounds == 1
    assert policy.safe_interactions is False


def test_zero_archive_depth_is_not_replaced_in_cli_or_gui_sources():
    root = pathlib.Path(__file__).resolve().parents[1]
    cli_source = (root / "cyoa_downloader_app/cli.py").read_text(encoding="utf-8")
    gui_source = (root / "cyoa_downloader_app/gui/app.py").read_text(encoding="utf-8")

    assert 'get("archive_max_depth", 30) or 30' not in cli_source
    assert 'get("archive_max_depth", 30) or 30' not in gui_source


def test_runtime_capture_recognizes_assets_with_missing_or_unusual_mime_types():
    assert _is_runtime_asset_response("https://example.test/module", "application/wasm")
    assert _is_runtime_asset_response("https://example.test/font.woff2", "application/octet-stream")
    assert _is_runtime_asset_response("https://example.test/card.webp", "")
    assert _is_runtime_asset_response("https://example.test/app.js?v=1", "text/plain")
    assert not _is_runtime_asset_response("https://example.test/page.html", "text/html")
    assert not _is_runtime_asset_response("http://[broken-ipv6/image.webp", "text/html")


def test_safe_interaction_allowlist_rejects_side_effect_controls():
    assert _is_safe_interaction_label("Load more")
    assert _is_safe_interaction_label("", aria_expanded_false=True)
    assert not _is_safe_interaction_label("Login")
    assert not _is_safe_interaction_label("Send comment")
    assert not _is_safe_interaction_label("Continue", in_form=True)
    assert not _is_safe_interaction_label("Show more", input_type="submit")


def test_cyoa_cafe_record_classification_and_file_url_encoding():
    static = {"id": "abc123", "collectionId": "collection1", "cyoa_pages": ["page one.webp"]}
    linked = {"id": "abc123", "iframe_url": "https://viewer.example/story/", "cyoa_pages": []}

    assert classify_cyoa_cafe_record(static) == "static_pages"
    assert classify_cyoa_cafe_record(linked) == "linked_viewer"
    assert build_cyoa_cafe_file_url(static, "page one.webp").endswith("/page%20one.webp")
    assert "%2F" in build_cyoa_cafe_file_url(static, "../escape/page.webp")


def test_cyoa_cafe_static_adapter_builds_backend_free_gallery(tmp_path, monkeypatch):
    from cyoa_downloader_app.download import cyoa_cafe_static as static_mod

    record = {
        "id": "abc123", "collectionId": "collection1", "title": "Static Test",
        "cyoa_pages": ["page.webp"], "cyoa_pages_preview": ["preview.webp"],
        "image": "cover.webp", "image_base64": "SECRET-LARGE-FIELD",
    }

    def fake_fetch(url, **_kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "image/webp"
        response.headers["Content-Length"] = "5"
        response._content = b"image"
        response._content_consumed = True
        return response

    monkeypatch.setattr(static_mod, "fetch_response", fake_fetch)
    manifest = download_cyoa_cafe_static_record(
        record, str(tmp_path), source_url="https://cyoa.cafe/game/abc123", max_workers=2,
    )

    assert manifest["detected_engine"] == "cyoa_cafe_static"
    assert len([item for item in manifest["downloaded"] if item["kind"] == "page"]) == 1
    assert (tmp_path / "index.html").is_file()
    assert "images/pages/" in (tmp_path / "index.html").read_text(encoding="utf-8")
    metadata = (tmp_path / "cyoa_cafe_metadata.json").read_text(encoding="utf-8")
    assert "SECRET-LARGE-FIELD" not in metadata


def test_auto_profiler_prefers_project_then_runtime_then_routes(tmp_path):
    project_downloader = _FakeDownloader(tmp_path / "project")
    pathlib.Path(project_downloader.start_html_local).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(project_downloader.start_html_local).write_text("<div id='app'></div>", encoding="utf-8")
    (pathlib.Path(project_downloader.output_folder) / "project.json").write_text(
        '{"rows":[],"pointTypes":[],"backpack":[]}', encoding="utf-8",
    )
    assert profile_archive_target(project_downloader).effective_strategy == "classic"

    runtime_downloader = _FakeDownloader(tmp_path / "runtime")
    pathlib.Path(runtime_downloader.start_html_local).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(runtime_downloader.start_html_local).write_text(
        '<div id="root"></div><script type="module" src="app.js"></script>', encoding="utf-8",
    )
    assert profile_archive_target(runtime_downloader).effective_strategy == "browser"

    route_downloader = _FakeDownloader(tmp_path / "routes")
    pathlib.Path(route_downloader.start_html_local).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(route_downloader.start_html_local).write_text(
        '<a href="/game/story/choice">Choice</a>', encoding="utf-8",
    )
    assert profile_archive_target(route_downloader).effective_strategy == "smart"


def test_auto_runner_records_project_decision_without_route_crawl(tmp_path):
    downloader = _FakeDownloader(tmp_path)
    pathlib.Path(downloader.start_html_local).write_text("<h1>Project viewer</h1>", encoding="utf-8")
    downloader.archive_auto_profile = project_archive_profile(
        downloader.start_url, "https://example.test/game/story/project.json",
    )

    manifest = run_archive_extensions(downloader, ArchivePolicy(strategy="auto"))

    assert manifest["requested_policy"]["strategy"] == "auto"
    assert manifest["policy"]["strategy"] == "classic"
    assert manifest["auto_profile"]["detected_engine"] == "project_json"
    assert manifest["runtime"] is None


def test_auto_project_profile_skips_heuristic_bundle_scan(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    downloader.archive_strategy = "auto"
    downloader.archive_auto_profile = project_archive_profile(
        downloader.start_url, "https://example.test/game/story/project.json",
    )
    downloader.ai_api_key = ""
    downloader.ai_provider = ""
    downloader.ai_mode = "off"
    downloader.ai_budget = None
    downloader.base_url = "https://example.test/game/"
    calls = []

    def fake_download_html(_url, destination):
        pathlib.Path(destination).write_text("<h1>offline</h1>", encoding="utf-8")

    monkeypatch.setattr(downloader, "_download_html", fake_download_html)
    monkeypatch.setattr(
        website_module, "_deep_scan_and_download_assets",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    downloader.download()

    assert calls == []
    assert pathlib.Path(downloader.start_html_local).is_file()


def test_dynamic_image_base_is_combined_without_emitting_wrong_bare_path():
    source = "const imageSrc = 'image/'; var imagesToLoad = ['card/A.webp', 'face/B.webp'];"
    inferred = _infer_dynamic_asset_paths(source)
    found = _scan_file_for_assets(
        source,
        "https://example.test/story/index.html",
        "https://example.test/story/",
        ".html",
    )

    assert inferred["card/A.webp"] == {"image/card/A.webp"}
    assert "https://example.test/story/image/card/A.webp" in found
    assert "https://example.test/story/card/A.webp" not in found


def test_integrity_validator_ignores_javascript_expressions_and_orphan_css(tmp_path):
    downloader = _bare_downloader(tmp_path)
    (tmp_path / "image/card").mkdir(parents=True)
    (tmp_path / "image/card/A.webp").write_bytes(b"image")
    (tmp_path / "index.html").write_text(
        '<script src="app.js"></script><link rel="stylesheet" href="site.css">',
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "const imageSrc='image/'; const imagesToLoad=['card/A.webp']; "
        "location.href = a.href; canvas.toDataURL();",
        encoding="utf-8",
    )
    (tmp_path / "site.css").write_text("body{background:url('image/card/A.webp')}", encoding="utf-8")
    # Runtime capture can leave an unreferenced duplicate stylesheet behind.
    (tmp_path / "orphan.css").write_text("@font-face{src:url('missing.woff2')}", encoding="utf-8")

    result = downloader.validate_integrity()

    assert result["missing"] == []
