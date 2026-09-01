from __future__ import annotations

import asyncio
import io
import pathlib
import threading
import time
from types import SimpleNamespace
from urllib.parse import quote

import requests
import pytest
import cyoa_downloader_app.download.website as website_module
import cyoa_downloader_app.network.runtime_capture as runtime_capture_module
from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError

from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download.archive_profiler import (
    ArchiveProfile, profile_archive_target, project_archive_profile,
)
from cyoa_downloader_app.download.archive_runner import run_archive_extensions
from cyoa_downloader_app.download.cyoa_cafe_static import download_cyoa_cafe_static_record
from cyoa_downloader_app.download.package import verify_output_package
from cyoa_downloader_app.download.route_crawler import RouteCrawler
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.download.asset_scan import _infer_dynamic_asset_paths, _scan_file_for_assets
from cyoa_downloader_app.network.runtime_capture import (
    RuntimeCaptureResult, _is_runtime_asset_response, _is_safe_interaction_label,
    capture_runtime_assets,
)
from cyoa_downloader_app.network.browser import BrowserFetchResult
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


def test_auto_website_switches_to_reusable_browser_transport_after_http_failure(tmp_path, monkeypatch):
    http_calls = []
    browser_calls = []

    class FakeBrowserSession:
        def fetch(self, url):
            browser_calls.append(url)
            return BrowserFetchResult(b"asset", {"content-type": "text/plain"}, 200, url)

        def close(self):
            return None

    monkeypatch.setattr(website_module, "BrowserFetchSession", FakeBrowserSession)
    monkeypatch.setattr(
        website_module,
        "fetch_response",
        lambda url, **_kwargs: http_calls.append(url) or None,
    )
    downloader = WebsiteDownloader(
        "https://example.test/game/story", str(tmp_path), archive_strategy="auto",
    )

    first = downloader._fetch("https://example.test/app.js")
    second = downloader._fetch("https://example.test/app.css")

    assert first is not None and first.content == b"asset"
    assert second is not None and second.content == b"asset"
    assert b"".join(first.iter_content(chunk_size=2)) == b"asset"
    assert http_calls == ["https://example.test/app.js"]
    assert browser_calls == ["https://example.test/app.js", "https://example.test/app.css"]


def test_website_browser_transport_and_auto_profiler_never_swallow_cancellation(tmp_path, monkeypatch):
    from cyoa_downloader_app.download import archive_profiler

    downloader = _bare_downloader(tmp_path)
    downloader.archive_strategy = "auto"
    downloader.archive_auto_profile = None
    downloader._browser_fetch_session = SimpleNamespace(
        fetch=lambda _url: (_ for _ in ()).throw(DownloadCancelledError("cancelled"))
    )
    with pytest.raises(DownloadCancelledError):
        downloader._fetch_with_browser("https://example.test/game/story")

    monkeypatch.setattr(
        downloader,
        "_download_html",
        lambda _url, local: pathlib.Path(local).write_text("<html></html>", encoding="utf-8"),
    )
    monkeypatch.setattr(
        archive_profiler,
        "profile_archive_target",
        lambda _downloader: (_ for _ in ()).throw(DownloadCancelledError("cancelled profile")),
    )
    with pytest.raises(DownloadCancelledError):
        downloader.download()


def test_browser_transport_defaults_text_to_utf8_without_charset(tmp_path, monkeypatch):
    expected = "Fantasy Roulette — locked 🔒"

    class FakeBrowserSession:
        def fetch(self, url):
            return BrowserFetchResult(
                expected.encode("utf-8"), {"content-type": "text/html"}, 200, url,
            )

        def close(self):
            return None

    monkeypatch.setattr(website_module, "BrowserFetchSession", FakeBrowserSession)
    monkeypatch.setattr(website_module, "fetch_response", lambda *_args, **_kwargs: None)
    downloader = WebsiteDownloader(
        "https://example.test/game/story", str(tmp_path), archive_strategy="auto",
    )

    response = downloader._fetch("https://example.test/game/story")

    assert response is not None
    assert response.encoding == "utf-8"
    assert website_module._safe_response_text(response) == expected


def test_pure_website_asset_failure_is_reported_to_progress(tmp_path, monkeypatch):
    events = []
    downloader = WebsiteDownloader(
        "https://example.test/game/", str(tmp_path), archive_strategy="classic",
    )
    monkeypatch.setattr(downloader, "_fetch", lambda _url: None)
    monkeypatch.setattr(website_module, "_emit_progress_event", lambda typ, **data: events.append({"type": typ, **data}))

    result = downloader._download_asset("https://example.test/game/missing.png")

    assert result is None
    assert downloader._failed_items == [{
        "url": "https://example.test/game/missing.png",
        "error": "request failed",
    }]
    assert events == [{
        "type": "file_failed",
        "name": "missing.png",
        "url": "https://example.test/game/missing.png",
        "error": "request failed",
    }]


def test_text_relocalization_does_not_rewrite_archive_manifest(tmp_path):
    manifest = tmp_path / "archive_manifest.json"
    original = '{"start_url":"https://example.test/game/story"}'
    manifest.write_text(original, encoding="utf-8")
    downloader = WebsiteDownloader(
        "https://example.test/game/story", str(tmp_path), archive_strategy="auto",
    )
    downloader._downloaded["https://example.test/game/story"] = str(tmp_path / "index.html")

    downloader.localize_existing_text_assets()

    assert manifest.read_text(encoding="utf-8") == original


def test_cli_report_is_safe_on_legacy_windows_encoding():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    _safe_console_print("PASS ✓ / FAIL ✗", file=stream)
    stream.flush()
    assert b"PASS OK / FAIL X" in raw.getvalue()


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


def test_successful_root_fallback_is_cached_for_original_reference(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://example.test/story/", str(tmp_path), archive_strategy="classic",
    )
    wrong = "https://example.test/story/js/assets/app.js"
    recovered = "https://example.test/story/assets/app.js"
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url == wrong:
            return None
        if url != recovered:
            raise AssertionError(url)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/javascript"
        response._content = b"window.archiveReady=true;"
        return response

    monkeypatch.setattr(downloader, "_fetch", fake_fetch)

    first = downloader._download_asset(
        "assets/app.js", preferred_kind="js",
        referrer_url="https://example.test/story/js/bundle.js",
    )
    second = downloader._download_asset(
        "assets/app.js", preferred_kind="js",
        referrer_url="https://example.test/story/js/bundle.js",
    )

    assert first is not None
    assert second == first
    assert calls == [wrong, recovered]
    assert downloader._downloaded[wrong] == first


def test_js_rewrite_ignores_in_progress_asset_cache_marker(tmp_path, monkeypatch):
    """Recursive bundle URLs must not pass the cache sentinel to os.path."""
    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    asset_url = "https://viewer.test/story/js/html-to-image.min.js"
    downloader._downloaded[asset_url] = website_module._ASSET_IN_PROGRESS
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: None)
    monkeypatch.setattr(
        downloader, "_download_runtime_template_assets", lambda *_a, **_k: None,
    )

    source = f'var source="dependency {asset_url}";'
    rewritten = downloader._rewrite_direct_urls(
        source,
        asset_url,
        str(tmp_path / "js" / "html-to-image.min.js"),
    )

    assert rewritten == source


def test_html_base_controls_root_assets_and_localization_removes_stale_sri(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    resolved = []

    def fake_download(value, *, preferred_kind="", referrer_url=None):
        resolved.append((
            downloader._normalize_remote_url(value, referrer_url), preferred_kind,
        ))
        return str(tmp_path / f"saved-{len(resolved)}.{preferred_kind or 'bin'}")

    monkeypatch.setattr(downloader, "_download_asset", fake_download)
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    downloader._download_html(
        downloader.start_url,
        html_text="""<!doctype html><html><head>
        <base href="https://cdn.test/app/">
        <link rel="stylesheet" href="/css/app.css" integrity="sha256-old-css">
        <link rel="alternate" href="project.json">
        <script src="/js/app.js" integrity="sha256-old-js"></script>
        </head><body></body></html>""",
    )

    saved = pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8")
    assert ("https://cdn.test/css/app.css", "css") in resolved
    assert ("https://cdn.test/app/project.json", "json") in resolved
    assert ("https://cdn.test/js/app.js", "js") in resolved
    assert "<base" not in saved
    assert "integrity=" not in saved


def test_missing_icon_keeps_normal_failed_asset_behavior(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )

    def fail_icon(value, *, referrer_url=None, **_kwargs):
        remote = downloader._normalize_remote_url(value, referrer_url)
        downloader._failed_items.append({"url": remote, "error": "404"})
        return None

    monkeypatch.setattr(downloader, "_download_asset", fail_icon)
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    downloader._download_html(
        downloader.start_url,
        html_text='<html><head><link rel="icon" href="/favicon.ico"></head><body></body></html>',
    )

    saved = pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8")
    assert 'rel="icon"' in saved
    assert 'href="https://viewer.test/favicon.ico"' in saved
    assert downloader._failed_items == [
        {"url": "https://viewer.test/favicon.ico", "error": "404"},
    ]


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


def test_asset_paths_reserve_case_insensitively_for_windows_portability(tmp_path):
    downloader = _bare_downloader(tmp_path)

    first = downloader._allocate_local_path(
        "https://example.test/game/Logo.png", content_type="image/png",
    )
    second = downloader._allocate_local_path(
        "https://example.test/game/logo.png", content_type="image/png",
    )

    assert first.casefold() != second.casefold()
    assert pathlib.Path(second).stem.endswith("_1")


def test_same_origin_unicode_asset_segment_is_bounded_by_bytes(tmp_path):
    downloader = _bare_downloader(tmp_path)
    long_name = "画" * 120 + ".png"

    local = downloader._allocate_local_path(
        f"https://example.test/game/{long_name}", content_type="image/png",
    )

    assert len(pathlib.Path(local).name.encode("utf-8")) <= 140
    assert pathlib.Path(local).suffix == ".png"


def test_cross_domain_basename_fallback_cannot_substitute_wrong_asset(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    cached = tmp_path / "js" / "app.js"
    cached.parent.mkdir()
    cached.write_text("window.fromOtherHost=true", encoding="utf-8")
    downloader._downloaded["https://other.test/assets/app.js"] = str(cached)
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: None)
    tag = website_module.BeautifulSoup('<script src="app.js"></script>', "html.parser").script

    localized = downloader._set_attr_local(
        tag, "src", "https://viewer.test/story/", str(tmp_path / "index.html"),
        preferred_kind="js",
    )

    assert localized is False
    assert tag["src"] == "https://viewer.test/story/app.js"


def test_unique_same_origin_bare_basename_fallback_is_preserved(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    cached = tmp_path / "js" / "polyfills.js"
    cached.parent.mkdir()
    cached.write_text("window.polyfills=true", encoding="utf-8")
    downloader._downloaded["https://viewer.test/story/js/polyfills.js"] = str(cached)
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_k: None)
    tag = website_module.BeautifulSoup(
        '<script src="polyfills.js"></script>', "html.parser",
    ).script

    localized = downloader._set_attr_local(
        tag, "src", "https://viewer.test/story/", str(tmp_path / "index.html"),
        preferred_kind="js",
    )

    assert localized is True
    assert tag["src"] == "js/polyfills.js"


def test_concurrent_same_asset_waits_for_leader_and_reuses_file(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    started = threading.Event()
    release = threading.Event()
    calls = []

    def delayed_fetch(url):
        calls.append(url)
        started.set()
        assert release.wait(timeout=5)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "image/png"
        response._content = b"shared-image-content"
        response._content_consumed = True
        return response

    monkeypatch.setattr(downloader, "_fetch", delayed_fetch)
    monkeypatch.setattr(website_module, "_ssrf_block_cross_origin", lambda *_a: False)
    asset_url = "https://viewer.test/story/images/shared.png"

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(downloader._download_asset, asset_url)
        assert started.wait(timeout=5)
        follower = pool.submit(downloader._download_asset, asset_url)
        time.sleep(0.05)
        assert not follower.done()
        release.set()
        first = leader.result(timeout=5)
        second = follower.result(timeout=5)

    assert first == second
    assert first is not None and pathlib.Path(first).read_bytes() == b"shared-image-content"
    assert calls == ["https://viewer.test/story/images/shared.png"]


def test_concurrent_failed_asset_wakes_follower_without_duplicate_request(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    started = threading.Event()
    release = threading.Event()
    calls = []

    def delayed_failure(url):
        calls.append(url)
        started.set()
        assert release.wait(timeout=5)
        return None

    monkeypatch.setattr(downloader, "_fetch", delayed_failure)
    monkeypatch.setattr(website_module, "_ssrf_block_cross_origin", lambda *_a: False)
    asset_url = "https://viewer.test/story/images/missing.png"

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(downloader._download_asset, asset_url)
        assert started.wait(timeout=5)
        follower = pool.submit(downloader._download_asset, asset_url)
        time.sleep(0.05)
        assert not follower.done()
        release.set()
        assert leader.result(timeout=5) is None
        assert follower.result(timeout=5) is None

    assert calls == [asset_url]
    assert downloader._download_events == {}
    assert downloader._download_owners == {}


def test_fetch_exception_releases_asset_reservation(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://viewer.test/story/", str(tmp_path), archive_strategy="classic",
    )
    asset_url = "https://viewer.test/story/images/crash.png"
    monkeypatch.setattr(website_module, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(
        downloader, "_fetch", lambda _url: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        downloader._download_asset(asset_url)

    assert downloader._downloaded[asset_url] is None
    assert downloader._download_events == {}
    assert downloader._download_owners == {}


def test_encoded_start_route_is_saved_relative_to_cyoa_root(tmp_path):
    downloader = _bare_downloader(tmp_path)
    downloader.start_url = "https://example.test/CYOA%27s/Fate%20NSFWCYOA/v1.5/"

    local = downloader._allocate_local_path(
        "https://example.test/CYOA%27s/Fate%20NSFWCYOA/v1.5/js/core.js",
        content_type="application/javascript",
    )

    assert pathlib.Path(local).relative_to(tmp_path).as_posix() == "js/core.js"


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


def test_route_rewrite_uses_original_base_and_preserves_fragments(tmp_path):
    downloader = _FakeDownloader(tmp_path)
    crawler = RouteCrawler(
        downloader, ArchivePolicy(strategy="smart", max_pages=10, max_depth=5),
    )
    source = (
        '<base href="https://example.test/game/story/chapters/">'
        '<a href="two#answer">Two</a><a href="#intro">Intro</a>'
    )
    page_url = downloader.start_url
    target_url = "https://example.test/game/story/chapters/two"
    assert crawler._links_from(source, page_url) == [target_url]

    root = pathlib.Path(downloader.start_html_local)
    root.write_text('<a href="two#answer">Two</a><a href="#intro">Intro</a>', encoding="utf-8")
    target = pathlib.Path(crawler._route_local_path(target_url))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<h1>Two</h1>", encoding="utf-8")

    crawler._rewrite_route_links({page_url: str(root), target_url: str(target)})

    saved = root.read_text(encoding="utf-8")
    assert "routes/chapters/two/index.html#answer" in saved
    assert 'href="#intro"' in saved


def test_route_local_path_handles_dot_only_suffix_with_query(tmp_path):
    crawler = RouteCrawler(
        _FakeDownloader(tmp_path), ArchivePolicy(strategy="smart"),
    )

    local = crawler._route_local_path(
        "https://example.test/game/story/../?ending=one",
    )

    assert pathlib.Path(local).name == "index.html"
    assert pathlib.Path(local).parent.name.startswith("route_")


def test_route_crawler_drops_navigation_only_return_to_query():
    assert RouteCrawler._canonicalize(
        "https://example.test/game/story?returnTo=%2Fcategory%2Fclassic"
    ) == "https://example.test/game/story"
    assert RouteCrawler._canonicalize(
        "https://example.test/game/story?ending=bad&returnTo=%2F"
    ) == "https://example.test/game/story?ending=bad"


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
    assert not _is_runtime_asset_response("https://example.test/missing.webp", "image/webp", 404)
    assert not _is_runtime_asset_response("https://example.test/error.js", "text/javascript", 500)


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


def test_cyoa_cafe_cancel_waits_for_active_file_workers(tmp_path, monkeypatch):
    from cyoa_downloader_app.download import cyoa_cafe_static as static_mod

    cancel_event = threading.Event()
    slow_worker_done = threading.Event()
    record = {
        "id": "abc123",
        "collectionId": "collection1",
        "cyoa_pages": ["page.webp"],
        "cyoa_pages_preview": ["preview.webp"],
    }

    def fake_download(_record, _folder, entry):
        kind, remote_name, relative = entry
        if remote_name == "preview.webp":
            cancel_event.set()
            return {"kind": kind, "source_name": remote_name, "local": relative}
        assert cancel_event.wait(2)
        time.sleep(0.05)
        slow_worker_done.set()
        raise DownloadCancelledError("cancelled in active worker")

    monkeypatch.setattr(static_mod, "_download_one", fake_download)
    cancellation.set_progress_event_sink(None, cancel_event)
    try:
        try:
            download_cyoa_cafe_static_record(
                record,
                str(tmp_path),
                source_url="https://cyoa.cafe/game/abc123",
                max_workers=2,
            )
        except DownloadCancelledError:
            pass
        else:
            raise AssertionError("cancellation should propagate")
        assert slow_worker_done.is_set()
    finally:
        cancellation.clear_progress_event_sink()


def test_runtime_capture_moves_sync_playwright_off_active_asyncio_loop(monkeypatch):
    caller_thread = threading.get_ident()
    captured = {}

    def fake_sync_capture(downloader, page_urls, settle_time_ms, **options):
        captured.update({
            "thread": threading.get_ident(),
            "downloader": downloader,
            "urls": tuple(page_urls),
            "settle": settle_time_ms,
            "options": options,
        })
        return RuntimeCaptureResult(pages_rendered=1)

    monkeypatch.setattr(
        runtime_capture_module, "_capture_runtime_assets_sync", fake_sync_capture,
    )
    downloader = object()

    async def invoke_from_running_loop():
        return capture_runtime_assets(
            downloader,
            (url for url in ["https://example.test/story/"]),
            settle_time_ms=321,
            capture_interactions=True,
            max_scroll_steps=7,
        )

    result = asyncio.run(invoke_from_running_loop())

    assert result.pages_rendered == 1
    assert captured["thread"] != caller_thread
    assert captured["downloader"] is downloader
    assert captured["urls"] == ("https://example.test/story/",)
    assert captured["settle"] == 321
    assert captured["options"]["capture_interactions"] is True
    assert captured["options"]["max_scroll_steps"] == 7


def test_runtime_capture_and_route_crawl_propagate_preexisting_cancellation(tmp_path):
    cancel_event = threading.Event()
    cancel_event.set()

    class Downloader:
        start_url = "https://example.test/game/"
        start_html_local = str(tmp_path / "index.html")
        output_folder = str(tmp_path)

        def _fetch(self, _url):
            raise AssertionError("cancelled crawl must not start network access")

    cancellation.set_progress_event_sink(None, cancel_event)
    try:
        with pytest.raises(DownloadCancelledError):
            capture_runtime_assets(Downloader(), [Downloader.start_url])
        with pytest.raises(DownloadCancelledError):
            RouteCrawler(
                Downloader(), ArchivePolicy(strategy="smart")
            ).crawl()
    finally:
        cancellation.clear_progress_event_sink()


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


def test_auto_classic_profile_runs_deep_scan_with_configured_workers(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://example.test/game/", str(tmp_path),
        archive_strategy="auto", max_workers=7,
    )
    calls = []

    def fake_download_html(_url, destination):
        pathlib.Path(destination).write_text(
            "const imgSrc='image/'; const imagesToLoad=['card/A.webp'];",
            encoding="utf-8",
        )

    monkeypatch.setattr(downloader, "_download_html", fake_download_html)
    monkeypatch.setattr(
        "cyoa_downloader_app.download.archive_profiler.profile_archive_target",
        lambda _downloader: ArchiveProfile(
            detected_engine="static_or_scannable",
            effective_strategy="classic",
            reason="fixture uses a statically scannable image array",
        ),
    )
    monkeypatch.setattr(
        website_module,
        "_deep_scan_and_download_assets",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {},
    )

    downloader.download()

    assert downloader.archive_auto_profile.effective_strategy == "classic"
    assert len(calls) == 1
    assert calls[0][1]["max_workers"] == 7


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


def test_dynamic_image_hint_lookup_scales_for_large_generated_pages():
    # Generated Twine/ICC pages can contain thousands of directory literals
    # and image metadata entries in one HTML file.  This exercises the exact
    # shape that previously caused an O(images * hints) finalization freeze.
    source = "\n".join(
        [f"const imageDir{i} = 'images/chapter{i}/';" for i in range(3_000)]
        + [f"item.img = 'card{i}.webp';" for i in range(3_000)]
    )

    started = time.perf_counter()
    inferred = _infer_dynamic_asset_paths(source)
    elapsed = time.perf_counter() - started

    assert inferred
    assert elapsed < 2.0


def test_dynamic_image_concat_scan_does_not_backtrack_on_large_html():
    # A long generated document with many path-like fragments but no matching
    # ``+ row.img`` suffix previously trapped the nested path regex in
    # catastrophic backtracking.
    source = "<script>" + ("const route = 'images/chapter/';" * 20_000) + "</script>"

    started = time.perf_counter()
    _infer_dynamic_asset_paths(source)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0


def test_css_comments_do_not_create_asset_requests_or_integrity_failures(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **_kwargs: calls.append(url) or None,
    )
    css = (
        "/* Do NOT add an @import here: because a non-leading @import is ignored. "
        "The URL is only documentation. */\n"
        "@import 'theme.css';\n.hero { background: url('hero.jpg'); }"
    )

    rewritten = downloader._process_css(
        css,
        "https://example.test/story/index.css",
        str(tmp_path / "index.css"),
    )

    assert rewritten == css
    assert "https://example.test/story/hero.jpg" in calls
    assert "https://example.test/story/theme.css" in calls
    assert not any(value.rstrip("/").endswith(("/is", "/here:")) for value in calls)

    (tmp_path / "index.html").write_text(
        f"<style>{css}</style>", encoding="utf-8"
    )
    integrity = downloader.validate_integrity()
    assert not any(ref.endswith(("→ here:", "→ is")) for ref in integrity["missing"])
    assert any(ref.endswith("→ hero.jpg") for ref in integrity["missing"])


def test_template_asset_placeholders_are_not_downloaded_as_literal_urls(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    source = 'const image = "${i.id}.jpg";'
    rewritten = downloader._rewrite_direct_urls(
        source,
        "https://example.test/story/index.html",
        str(tmp_path / "index.html"),
    )

    assert rewritten == source
    assert calls == []


def test_escaped_next_flight_urls_are_not_rewritten_or_unescaped(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda *args, **kwargs: calls.append((args, kwargs)) or str(tmp_path / "app.js"),
    )
    source = (
        r'self.__next_f.push([1,"2:I[1,[\"/_next/static/chunks/app.js\"],\"\"]\n"])'
    )

    rewritten = downloader._rewrite_direct_urls(
        source,
        "https://example.test/game/story",
        str(tmp_path / "index.html"),
    )

    assert rewritten == source
    assert calls == []


def test_next_chunk_uses_root_local_reference_for_downloaded_image(tmp_path):
    downloader = _bare_downloader(tmp_path)
    image = tmp_path / "external" / "images" / "patreon.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    chunk = tmp_path / "_next" / "static" / "chunks" / "app.js"
    chunk.parent.mkdir(parents=True)
    chunk.write_text("", encoding="utf-8")
    downloader._downloaded = {}
    downloader._downloaded["https://cdn.example/patreon.png"] = str(image)

    rewritten = downloader._rewrite_known_downloaded_urls(
        'const src="https://cdn.example/patreon.png";',
        "https://example.test/_next/static/chunks/app.js",
        str(chunk),
    )

    assert rewritten == 'const src="/external/images/patreon.png";'


def test_download_html_adds_narrow_offline_dice_fallback(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://example.test/game/dice", str(tmp_path))
    monkeypatch.setattr(downloader, "_download_runtime_template_assets", lambda *_args: None)
    html = (
        '<main><div role="status" aria-label="Dice results: Z ?"><b>Z</b><b>–</b></div>'
        '<button aria-label="Roll dice again">Roll again</button></main>'
    )

    downloader._download_html(
        "https://example.test/game/dice",
        str(tmp_path / "index.html"),
        html,
    )

    saved = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "data-cyoa-offline-dice-fallback" in saved
    assert "Roll dice again" in saved


def test_runtime_incarnation_template_downloads_concrete_ids(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.jpg"),
    )
    source = "const DATA={incarnations:[{id:'aang'},{id:'korra'}]}; const x=\"${i.id}.jpg\";"

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/atla/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/atla/aang.jpg",
        "https://example.test/atla/korra.jpg",
    ]


def test_runtime_template_prefetch_is_not_bound_to_a_named_data_shape(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.webp"),
    )
    source = (
        "const cards=[{slug:'one'},{slug:'two'}]; "
        "const src=`cards/${entry.slug}.webp`;"
    )

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/story/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/story/cards/one.webp",
        "https://example.test/story/cards/two.webp",
    ]


def test_runtime_numeric_range_prefetches_all_concrete_assets(tmp_path, monkeypatch):
    downloader = _bare_downloader(tmp_path)
    calls = []
    monkeypatch.setattr(
        downloader,
        "_download_asset",
        lambda url, **kwargs: calls.append((url, kwargs)) or str(tmp_path / "asset.png"),
    )
    source = (
        "const randomIndex = Math.floor(Math.random() * 3) + 1; "
        "comic.src = 'comics/' + randomIndex + '.png';"
    )

    downloader._download_runtime_template_assets(
        source,
        "https://example.test/story/",
    )

    assert [url for url, _ in calls] == [
        "https://example.test/story/comics/1.png",
        "https://example.test/story/comics/2.png",
        "https://example.test/story/comics/3.png",
    ]


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


def test_integrity_accepts_missing_legacy_font_formats_when_woff2_exists(tmp_path):
    downloader = _bare_downloader(tmp_path)
    (tmp_path / "css").mkdir()
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "icons.woff2").write_bytes(b"font")
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="css/icons.css">', encoding="utf-8"
    )
    (tmp_path / "css" / "icons.css").write_text(
        "@font-face{font-family:Icons;src:url('../fonts/icons.eot');"
        "src:url('../fonts/icons.woff2') format('woff2'),"
        "url('../fonts/icons.woff') format('woff'),"
        "url('../fonts/icons.ttf') format('truetype')}",
        encoding="utf-8",
    )

    result = downloader.validate_integrity()

    assert result["missing"] == []
