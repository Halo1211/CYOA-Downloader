"""Offline regressions for efficient, resumable website archives."""

import functools
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from bs4 import BeautifulSoup

from cyoa_downloader_app.core import atomic_io, cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import archive_runner, image_pipeline, website
from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download.asset_scan import _srcset_urls
from cyoa_downloader_app.download.route_crawler import RouteCrawler

START = "https://example.test/game/"
POLICY = ArchivePolicy(strategy="smart", max_pages=20)


def response(url, text, content_type="text/html"):
    result = requests.Response()
    result.status_code = 200
    result.url = url
    result._content = text if isinstance(text, bytes) else text.encode("utf-8")
    result._content_consumed = True
    result.headers["Content-Type"] = content_type
    result.close = Mock()
    return result


@pytest.fixture
def downloader(tmp_path, monkeypatch):
    monkeypatch.setattr(website, "_load_settings", lambda: {})
    instance = website.WebsiteDownloader(START, str(tmp_path), archive_strategy="smart")
    monkeypatch.setattr(instance, "_download_runtime_template_assets", lambda *_a: None)
    yield instance
    instance.close()
    instance.session.close()


def test_crawl_reuses_successful_entry_without_fetch_or_localization(downloader, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        return response(url, '<a href="choice#answer">Choose</a>' if url == START else '<h1 id="answer">Choice</h1>')

    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(START)
    localize = Mock(wraps=downloader.download_html_page)
    monkeypatch.setattr(downloader, "download_html_page", localize)
    result = RouteCrawler(downloader, POLICY).crawl()
    assert calls == [START, START + "choice"]
    assert localize.call_count == 1
    assert len(result.pages) == 2
    assert 'href="routes/choice/index.html#answer"' in Path(downloader.start_html_local).read_text("utf-8")
    assert downloader._entry_source is None


def test_entry_redirect_resolves_assets_from_response_url(downloader, monkeypatch):
    requested = START.rstrip("/")
    downloader.start_url = requested
    fetched = response(START, '<img src="images/hero.png">')
    monkeypatch.setattr(downloader, "_fetch", lambda _u: fetched)
    assets = []
    monkeypatch.setattr(downloader, "_download_asset", lambda url, **kw: assets.append(downloader._normalize_remote_url(url, kw.get("referrer_url"))))
    downloader._download_html(requested)
    assert assets == [START + "images/hero.png"]
    fetched.close.assert_called_once()


def test_redirected_routes_resolve_assets_and_links_and_keep_incoming_alias(downloader, monkeypatch):
    pages = {
        START: (START, '<a href="chapter">Chapter</a>'),
        START + "chapter": (START + "chapter/", '<img src="hero.png"><a href="next#end">Next</a>'),
        START + "chapter/next": (START + "chapter/next", '<h1 id="end">End</h1>'),
    }
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(*pages[url]))
    assets = []
    monkeypatch.setattr(downloader, "_download_asset", lambda url, **kw: assets.append(downloader._normalize_remote_url(url, kw.get("referrer_url"))))
    result = RouteCrawler(downloader, POLICY).crawl()
    assert START + "chapter/next" in result.pages
    assert assets == [START + "chapter/hero.png"]
    chapter = Path(result.pages[START + "chapter"]).read_text("utf-8")
    assert 'href="../chapter/next/index.html#end"' not in chapter
    assert 'href="next/index.html#end"' in chapter


@pytest.mark.parametrize("content_type", ["image/png", "application/zip", "application/json"])
def test_non_html_links_do_not_consume_page_budget(downloader, monkeypatch, content_type):
    sources = {
        START: response(START, '<a href="asset">Asset</a><a href="choice">Choice</a>'),
        START + "asset": response(START + "asset", "not HTML", content_type),
        START + "choice": response(START + "choice", "<h1>Choice</h1>"),
    }
    monkeypatch.setattr(downloader, "_fetch", sources.get)
    result = RouteCrawler(downloader, ArchivePolicy(strategy="smart", max_pages=2)).crawl()
    assert set(result.pages) == {START, START + "choice"}
    assert len(result.failed) == 1
    assert "not HTML" in result.failed[0]["error"]
    sources[START + "asset"].close.assert_called_once()


@pytest.mark.parametrize("content_type", ["", "application/xhtml+xml", "text/html; charset=utf-8"])
def test_html_and_unspecified_types_remain_supported(downloader, monkeypatch, content_type):
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, "<h1>Story</h1>", content_type))
    assert set(RouteCrawler(downloader, POLICY).crawl().pages) == {START}


def test_duplicate_seeds_are_fetched_once(downloader, monkeypatch):
    fetch = Mock(side_effect=lambda url: response(url, "<h1>Story</h1>"))
    monkeypatch.setattr(downloader, "_fetch", fetch)
    assert len(RouteCrawler(downloader, POLICY).crawl(seed_urls=[START, START]).pages) == 1
    fetch.assert_called_once_with(START)


def test_explicit_empty_seeds_do_not_restart_archive(downloader, monkeypatch):
    monkeypatch.setattr(downloader, "_fetch", Mock(side_effect=AssertionError("unexpected fetch")))
    assert RouteCrawler(downloader, POLICY).crawl(seed_urls=[]).pages == {}


def test_existing_localized_links_are_not_reinterpreted(downloader, tmp_path):
    root = Path(downloader.start_html_local)
    root.write_text('<a data-cyoa-local-route="1" href="routes/one/index.html#end">One</a>', "utf-8")
    wrong_target = tmp_path / "wrong.html"
    wrong_target.write_text("<h1>Wrong</h1>", "utf-8")
    RouteCrawler(downloader, POLICY)._rewrite_route_links({START: str(root), START + "routes/one/index.html": str(wrong_target)})
    assert 'href="routes/one/index.html#end"' in root.read_text("utf-8")


@pytest.mark.parametrize("name", ["chapter#one", "chapter%23one", "chapter%2520one"])
def test_resume_uses_literal_manifest_paths_and_retries_failed_pages(tmp_path, monkeypatch, name):
    local = tmp_path / "routes" / name / "index.html"
    local.parent.mkdir(parents=True)
    local.write_text("<h1>Saved</h1>", "utf-8")
    manifest = {
        "pages": [{"url": START + "saved", "local": local.relative_to(tmp_path).as_posix()}],
        "route_failures": [{"url": START + "retry"}, {"url": "https://outside.test/"}],
    }
    (tmp_path / "archive_manifest.json").write_text(json.dumps(manifest), "utf-8")
    calls = []

    def fetch(_self, url):
        calls.append(url)
        return response(url, "<h1>Recovered</h1>")

    monkeypatch.setattr(website.WebsiteDownloader, "_fetch", fetch)
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert calls == [START + "retry"]
    assert {page["url"] for page in result["pages"]} == {START + "saved", START + "retry"}
    assert result["route_failures"] == []


def test_resume_keeps_html_base_at_crawl_boundary(downloader, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, '<base href="chapters/"><a href="two#end">Two</a>'))
    manifest = archive_runner.run_archive_extensions(downloader, ArchivePolicy(strategy="smart", max_depth=0))
    assert manifest["route_link_bases"][START] == START + "chapters/"
    calls = []

    def fetch(_self, url):
        calls.append(url)
        return response(url, '<h1 id="end">End</h1>')

    monkeypatch.setattr(website.WebsiteDownloader, "_fetch", fetch)
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert calls == [START + "chapters/two"]
    assert len(result["pages"]) == 2
    assert 'href="routes/chapters/two/index.html#end"' in Path(downloader.start_html_local).read_text("utf-8")


def test_audio_script_scan_reads_unchanged_files_once_and_detects_changes(downloader, tmp_path, monkeypatch):
    script = tmp_path / "app.js"
    script.write_text("function unrelated() {}", "utf-8")
    downloader._downloaded[START + "app.js"] = str(script)
    read = Path.read_text
    calls = []

    def counted(path, *args, **kwargs):
        calls.append(path)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    for _ in range(40):
        downloader._patch_local_audio_scripts()
    assert calls == [script]
    script.write_text('function playVideo(id) { return "youtube.com/embed/" + id; }', "utf-8")
    downloader._patch_local_audio_scripts()
    assert "__cyoaLocalAudioPatch" in read(script, "utf-8")
    downloader._patch_local_audio_scripts()
    assert len(calls) == 2


def test_waiting_asset_follower_observes_cancellation(downloader, monkeypatch):
    cancel = threading.Event()
    waits = []

    class Pending:
        def wait(self, timeout):
            waits.append(timeout)
            cancel.set()
            return False

    url = START + "pending.png"
    downloader._downloaded[url] = website._ASSET_IN_PROGRESS
    downloader._download_owners[url] = -1
    downloader._download_events[url] = Pending()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", cancel)
    with pytest.raises(DownloadCancelledError):
        downloader._download_asset(url)
    assert waits and max(waits) <= 0.1


def test_failed_html_commit_preserves_previous_archive(downloader, monkeypatch):
    target = Path(downloader.start_html_local)
    target.write_text("previous archive", "utf-8")
    monkeypatch.setattr(atomic_io.os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        downloader._download_html(START, html_text="<h1>New archive</h1>")
    assert target.read_text("utf-8") == "previous archive"
    assert list(target.parent.glob("*.part")) == []


def test_resume_rejects_paths_outside_archive(tmp_path, monkeypatch):
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "pages": [{"url": START, "local": "../index.html"}],
    }), "utf-8")
    monkeypatch.setattr(website.WebsiteDownloader, "_fetch", Mock(side_effect=AssertionError("unexpected fetch")))
    assert archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)["pages"] == []


def test_resume_recovers_deleted_manifest_page(tmp_path, monkeypatch):
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "pages": [{"url": START + "deleted", "local": "routes/deleted/index.html"}],
    }), "utf-8")
    calls = []

    def fetch(_self, url):
        calls.append(url)
        return response(url, "<h1>Restored</h1>")

    monkeypatch.setattr(website.WebsiteDownloader, "_fetch", fetch)
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert calls == [START + "deleted"]
    assert len(result["pages"]) == 1
    assert (tmp_path / "routes/deleted/index.html").is_file()


def test_entry_reuse_falls_back_when_saved_file_is_missing(downloader, monkeypatch):
    fetch = Mock(side_effect=lambda url: response(url, "<h1>Restored</h1>"))
    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(START)
    Path(downloader.start_html_local).unlink()
    assert set(RouteCrawler(downloader, POLICY).crawl().pages) == {START}
    assert fetch.call_count == 2


def test_capped_resume_keeps_unattempted_failure_for_later(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<h1>Saved</h1>", "utf-8")
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "pages": [{"url": START, "local": "index.html"}],
        "route_failures": [{"url": START + "retry"}],
    }), "utf-8")
    fetch = Mock(side_effect=lambda _self, url: response(url, "<h1>Recovered</h1>"))
    monkeypatch.setattr(website.WebsiteDownloader, "_fetch", lambda self, url: fetch(self, url))
    capped = archive_runner.resume_existing_archive(str(tmp_path), START, ArchivePolicy(strategy="smart", max_pages=1))
    fetch.assert_not_called()
    assert capped["pending_routes"] == [START + "retry"]
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert fetch.call_count == 1
    assert len(result["pages"]) == 2
    assert result["pending_routes"] == []


@pytest.mark.parametrize("markup,selector,attribute", [
    ('<img src="icons%23set%25.svg#mark">', "img", "src"),
    ('<img srcset="icons%23set%25.svg#mark 2x">', "img", "srcset"),
    ('<svg><use href="icons%23set%25.svg#mark"></use></svg>', "use", "href"),
    ('<svg><image xlink:href="icons%23set%25.svg#mark"></image></svg>', "image", "xlink:href"),
])
def test_svg_references_keep_literal_filename_and_fragment(downloader, monkeypatch, markup, selector, attribute):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return response(url, '<svg xmlns="http://www.w3.org/2000/svg"><symbol id="mark"/></svg>', "image/svg+xml")

    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(START, html_text=markup)
    saved = BeautifulSoup(Path(downloader.start_html_local).read_text("utf-8"), "html.parser")
    value = saved.select_one(selector)[attribute]
    assert value == "icons%23set%25.svg#mark" + (" 2x" if attribute == "srcset" else "")
    assert fetched == [START + "icons%23set%25.svg"]
    assert (Path(downloader.output_folder) / "icons#set%.svg").is_file()
    assert downloader.validate_integrity()["missing"] == []


@pytest.mark.parametrize("source", [
    'body { filter: url("icons(1).svg#mark"); }',
    '@import "theme light.css" screen and (min-width: 1px);',
])
def test_css_quoted_filenames_and_fragments_are_preserved(downloader, tmp_path, monkeypatch, source):
    calls = []

    def fetch(url):
        calls.append(url)
        return response(url, "/* theme */" if "css" in url else "<svg/>", "text/css" if "css" in url else "image/svg+xml")

    monkeypatch.setattr(downloader, "_fetch", fetch)
    css_path = tmp_path / "theme.css"
    rewritten = downloader._process_css(source, START + "theme.css", str(css_path))
    if "@import" in source:
        assert rewritten == '@import url("theme%20light.css") screen and (min-width: 1px);'
        assert calls == [START + "theme light.css"]
    else:
        assert rewritten == 'body { filter: url("icons%281%29.svg#mark"); }'
        assert calls == [START + "icons(1).svg"]


def test_known_script_urls_keep_fragment_and_encode_local_paths(downloader, tmp_path):
    asset = tmp_path / "icons#set%.svg"
    asset.write_text("<svg/>", "utf-8")
    downloader._downloaded[START + "icons%23set%25.svg"] = str(asset)
    js_path = tmp_path / "_next/static/app.js"
    assert downloader._rewrite_known_downloaded_urls(
        f'const icon = "{START}icons%23set%25.svg#mark";', START, str(js_path),
    ) == 'const icon = "/icons%23set%25.svg#mark";'


def test_relative_script_svg_fragment_is_downloaded(downloader, tmp_path, monkeypatch):
    fetched = []
    monkeypatch.setattr(downloader, "_fetch", lambda url: fetched.append(url) or response(url, "<svg/>", "image/svg+xml"))
    result = downloader._rewrite_direct_urls('const icon = "icons.svg#mark";', START, str(tmp_path / "loader.js"))
    assert fetched == [START + "icons.svg"]
    assert result == 'const icon = "icons.svg#mark";'


@pytest.mark.parametrize("content_type", ["text/html", "application/xhtml+xml"])
def test_iframe_document_gets_separate_file_and_local_assets(downloader, tmp_path, monkeypatch, content_type):
    pages = {
        START + "frame": response(START + "frames/", '<img src="hero.png">', content_type),
        START + "frames/hero.png": response(START + "frames/hero.png", "PNG", "image/png"),
    }
    fetched = []
    monkeypatch.setattr(downloader, "_fetch", lambda url: fetched.append(url) or pages.get(url))
    downloader._download_html(START, html_text='<h1>Parent</h1><iframe src="frame#chapter" sandbox="allow-scripts" loading="lazy"></iframe>')
    parent = BeautifulSoup((tmp_path / "index.html").read_text("utf-8"), "html.parser")
    assert parent.h1.text == "Parent"
    frame = parent.iframe
    assert frame["sandbox"] == ["allow-scripts"] and frame["loading"] == "lazy"
    assert frame["src"] == "frame.html#chapter"
    assert (tmp_path / "frame.html").is_file()
    assert fetched == [START + "frame", START + "frames/hero.png"]
    assert downloader.validate_integrity()["missing"] == []


@pytest.mark.parametrize("markup", [
    '<iframe src="https://outside.test/embed"></iframe>',
    '<iframe data-src="https://outside.test/embed"></iframe>',
    '<iframe src="frame" srcdoc="&lt;h1&gt;Inline&lt;/h1&gt;"></iframe>',
])
def test_external_and_srcdoc_frames_preserve_authored_markup(downloader, monkeypatch, markup):
    fetch = Mock(side_effect=AssertionError("frame should not be fetched"))
    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(START, html_text=markup)
    fetch.assert_not_called()
    saved = BeautifulSoup(Path(downloader.start_html_local).read_text("utf-8"), "html.parser")
    assert saved.iframe.attrs == BeautifulSoup(markup, "html.parser").iframe.attrs


@pytest.mark.parametrize("kind,url,body,expected", [
    ("css", "style.css", 'body {background:url(hero.png)}', "assets/hero.png"),
    ("js", "loader.js", 'const icon="hero.png";', "assets/hero.png"),
])
def test_redirected_text_assets_use_final_dependency_base(downloader, tmp_path, monkeypatch, kind, url, body, expected):
    calls = []

    def fetch(candidate):
        calls.append(candidate)
        if candidate == START + url:
            return response(START + "assets/" + url, body, "text/css" if kind == "css" else "application/javascript")
        if candidate == START + "assets/hero.png":
            return response(candidate, "PNG", "image/png")
        return None

    monkeypatch.setattr(downloader, "_fetch", fetch)
    local = downloader._download_asset(START + url, preferred_kind=kind)
    assert calls == [START + url, START + "assets/hero.png"]
    assert expected in Path(local).read_text("utf-8")


def test_html_asset_does_not_overwrite_entry(downloader, tmp_path, monkeypatch):
    root = tmp_path / "index.html"
    root.write_text("<h1>Parent</h1>", "utf-8")
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, "<h1>Child</h1>"))
    child = downloader._download_asset(START + "child.html")
    assert Path(child) != root
    assert root.read_text("utf-8") == "<h1>Parent</h1>"
    assert "Child" in Path(child).read_text("utf-8")


def test_asset_allocation_failure_closes_response_and_releases_waiters(downloader, monkeypatch):
    fetched = response(START + "hero.png", "PNG", "image/png")
    monkeypatch.setattr(downloader, "_fetch", lambda _url: fetched)
    monkeypatch.setattr(downloader, "_allocate_local_path", Mock(side_effect=OSError("cannot allocate output")))
    with pytest.raises(OSError, match="cannot allocate output"):
        downloader._download_asset(START + "hero.png")
    fetched.close.assert_called_once()
    assert downloader._download_events == {}
    assert downloader._download_owners == {}
    assert downloader._downloaded[START + "hero.png"] is None


def test_frame_cycle_reuses_active_parent_without_fetching_it(downloader, tmp_path, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        return response(url, f'<iframe src="{START}"></iframe>')

    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(START, html_text='<h1>Root</h1><iframe src="child.html"></iframe>')
    assert calls == [START + "child.html"]
    assert '<h1>Root</h1>' in (tmp_path / "index.html").read_text("utf-8")
    assert 'src="index.html"' in (tmp_path / "child.html").read_text("utf-8")
    assert downloader._html_state.pages == {}


def test_unending_frame_chain_has_bounded_depth(downloader, tmp_path, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        index = int(url.rsplit("frame", 1)[1])
        return response(url, f'<iframe src="frame{index + 1}"></iframe>')

    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(START, html_text='<h1>Root</h1><iframe src="frame1"></iframe>')
    assert len(calls) == 16
    assert len(list(tmp_path.glob("*.html"))) == 16
    assert downloader._download_events == {}
    assert downloader._html_state.pages == {}
    assert any("limit reached" in item["error"] for item in downloader._failed_items)


@pytest.mark.parametrize("markup,expected", [
    ('<svg><use href="missing.svg#mark"></use></svg>', "missing.svg#mark"),
    ('<object data="missing.html"></object>', "missing.html"),
    ('<iframe src="unused.html" srcdoc="&lt;h1&gt;Inline&lt;/h1&gt;"></iframe>', None),
])
def test_integrity_checks_embedded_dependencies_and_honors_srcdoc(downloader, tmp_path, markup, expected):
    (tmp_path / "index.html").write_text(markup, "utf-8")
    result = downloader.validate_integrity()
    if expected:
        assert any(expected in item for item in result["missing"])
    else:
        assert result["missing"] == []


def test_nested_css_imports_are_fetched_once_and_keep_media_conditions(downloader, tmp_path, monkeypatch):
    bodies = {
        START + "theme.css": '@import "nested/theme light.css" screen; body {filter:url("icons.svg#one");background:url("icons.svg#two")}',
        START + "nested/theme light.css": '@import "../theme.css"; body {color:red}',
        START + "icons.svg": "<svg/>",
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return response(url, bodies[url], "image/svg+xml" if url.endswith(".svg") else "text/css")

    monkeypatch.setattr(downloader, "_fetch", fetch)
    saved = downloader._download_asset(START + "theme.css")
    assert calls == list(bodies)
    text = Path(saved).read_text("utf-8")
    assert '@import url("nested/theme%20light.css") screen;' in text
    assert 'url("icons.svg#one")' in text and 'url("icons.svg#two")' in text
    assert downloader._download_events == {}


@pytest.mark.parametrize("prefix", [START, "https://cdn.example.test/"])
@pytest.mark.parametrize("remote_name,literal_name", [
    ("icons%23one.svg", "icons#one.svg"),
    ("icons%2523one.svg", "icons%23one.svg"),
    ("image%20one.png", "image one.png"),
])
def test_asset_names_are_decoded_once_for_local_and_external_hosts(downloader, monkeypatch, prefix, remote_name, literal_name):
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, "image", "image/png"))
    local = downloader._download_asset(prefix + remote_name)
    assert Path(local).name == literal_name


def test_child_named_index_cannot_claim_parent_output(downloader, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, "<h1>Child</h1>"))
    downloader._download_html(START, html_text='<h1>Parent</h1><iframe src="index.html"></iframe>')
    assert '<h1>Parent</h1>' in (tmp_path / "index.html").read_text("utf-8")
    assert (tmp_path / "index_1.html").read_text("utf-8") == '<h1>Child</h1>'


@pytest.mark.parametrize("filename", ["style.css", "index.html"])
def test_css_processing_skips_javascript_inference(downloader, tmp_path, monkeypatch, filename):
    infer = Mock(side_effect=AssertionError("CSS must not run JavaScript inference"))
    monkeypatch.setattr(website, "_infer_dynamic_asset_paths", infer)
    css = 'body { color: red; --label: "A static label"; }'
    assert downloader._process_css(css, START, str(tmp_path / filename)) == css
    infer.assert_not_called()


def test_javascript_inference_remains_enabled(downloader, tmp_path, monkeypatch):
    infer = Mock(return_value={})
    monkeypatch.setattr(website, "_infer_dynamic_asset_paths", infer)
    text = 'const label = "A static label";'
    assert downloader._rewrite_direct_urls(text, START, str(tmp_path / "loader.js")) == text
    infer.assert_called_once_with(text)


def test_redirected_entry_frame_cycle_reuses_final_url_alias(downloader, tmp_path, monkeypatch):
    requested = START.rstrip("/")
    downloader.start_url = requested
    fetch = Mock(side_effect=lambda _url: response(START, '<h1>Root</h1><iframe src="./"></iframe>'))
    monkeypatch.setattr(downloader, "_fetch", fetch)
    downloader._download_html(requested)
    fetch.assert_called_once_with(requested)
    assert list(tmp_path.rglob("*.html")) == [tmp_path / "index.html"]
    assert 'src="index.html"' in (tmp_path / "index.html").read_text("utf-8")
    assert downloader._html_state.pages == {}
    assert downloader._html_state.depth == 0


@pytest.mark.parametrize("attribute", ["src", "data-src"])
def test_binary_iframe_document_keeps_original_bytes(downloader, tmp_path, monkeypatch, attribute):
    data = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n<stream>raw&data</stream>\n%%EOF\n"
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, data, "application/pdf"))
    downloader._download_html(START, html_text=f'<iframe {attribute}="document.pdf"></iframe>')
    assert (tmp_path / "document.pdf").read_bytes() == data


@pytest.mark.parametrize("attribute", ["srcset", "imagesrcset", "data-srcset"])
@pytest.mark.parametrize("source,expected,calls", [
    ("first.png 1x,data:image/png;base64,AAAA 2x", "local/first.png 1x, data:image/png;base64,AAAA 2x", ["first.png"]),
    ("data:image/png;base64,AAAA 1x,second.png 2x", "data:image/png;base64,AAAA 1x, local/second.png 2x", ["second.png"]),
    ("crop.png?rect=1,2,3,4 1x, second.png 2x", "local/crop.png 1x, local/second.png 2x", ["crop.png?rect=1,2,3,4", "second.png"]),
])
def test_responsive_candidates_preserve_data_urls_and_comma_queries(downloader, tmp_path, monkeypatch, attribute, source, expected, calls):
    fetched = []

    def download(url, **_kwargs):
        fetched.append(url)
        return str(tmp_path / "local" / url.split("?", 1)[0])

    monkeypatch.setattr(downloader, "_download_asset", download)
    tag = {attribute: source}
    assert downloader._set_attr_local(tag, attribute, START, str(tmp_path / "index.html"), preferred_kind="images")
    assert tag[attribute] == expected
    assert fetched == calls


def test_responsive_parser_does_not_copy_remaining_input_per_character(downloader, tmp_path, monkeypatch):
    copied = []

    class TrackedText(str):
        def __getitem__(self, key):
            value = super().__getitem__(key)
            if isinstance(key, slice):
                copied.append(len(value))
            return value

    source = TrackedText(", ".join(f"image{index}.png {index + 1}w" for index in range(100)))
    monkeypatch.setattr(downloader, "_download_asset", lambda *_a, **_kw: None)
    assert not downloader._set_attr_local({"srcset": source}, "srcset", START, str(tmp_path / "index.html"))
    assert sum(copied) <= 4 * len(source)


@pytest.mark.parametrize("attribute", ["srcset", "imagesrcset", "data-srcset"])
def test_integrity_does_not_treat_data_payload_as_a_file(downloader, tmp_path, attribute):
    (tmp_path / "hero.png").write_bytes(b"PNG")
    (tmp_path / "index.html").write_text(f'<img {attribute}="data:image/png;base64,AAAA 1x, hero.png 2x">', "utf-8")
    report = downloader.validate_integrity()
    assert report["missing"] == []
    assert any("hero.png" in item for item in report["ok"])


def test_integrity_checks_all_responsive_attributes(downloader, tmp_path):
    (tmp_path / "hero.png").write_bytes(b"PNG")
    (tmp_path / "index.html").write_text('<img srcset="hero.png 1x" data-srcset="missing.png 2x">', "utf-8")
    assert any("missing.png" in item for item in downloader.validate_integrity()["missing"])


def test_scanner_keeps_commas_in_future_descriptor_tokens():
    assert list(_srcset_urls("first.png future(1,2), second.png 2x")) == ["first.png", "second.png"]


@pytest.mark.parametrize("name", ["image#one.png", "image%23one.png", "image%2520one.png"])
def test_deep_scan_cache_keeps_literal_file_paths(downloader, tmp_path, monkeypatch, name):
    folder = tmp_path / "images"
    folder.mkdir()
    saved = folder / name
    saved.write_bytes(b"deep scan")
    url = START + "remote.png"
    downloader._register_deep_scan_results({url: saved.relative_to(tmp_path).as_posix()})
    monkeypatch.setattr(downloader, "_fetch", Mock(side_effect=AssertionError("deep-scanned file fetched again")))
    assert downloader._download_asset(url) == str(saved)


def test_deep_scan_cache_reserves_existing_asset_names(downloader, tmp_path, monkeypatch):
    folder = tmp_path / "images"
    folder.mkdir()
    original = folder / "hero.png"
    original.write_bytes(b"original deep scan")
    downloader._register_deep_scan_results({"https://first.example.test/hero.png": "images/hero.png"})
    monkeypatch.setattr(downloader, "_fetch", lambda url: response(url, b"another image", "image/png"))
    saved = downloader._download_asset("https://second.example.test/hero.png")
    assert Path(saved) != original
    assert original.read_bytes() == b"original deep scan"


@pytest.mark.parametrize("unsafe", ["../hero.png", "/images/hero.png", "images/../hero.png"])
def test_deep_scan_cache_rejects_unsafe_paths_instead_of_aliasing(downloader, tmp_path, unsafe):
    (tmp_path / "images").mkdir()
    (tmp_path / "hero.png").write_bytes(b"root file")
    (tmp_path / "images/hero.png").write_bytes(b"nested file")
    url = START + "untrusted.png"
    downloader._register_deep_scan_results({url: unsafe})
    assert url not in downloader._downloaded


@pytest.mark.parametrize("prefix", [START, "https://cdn.example.test/"])
@pytest.mark.parametrize("remote_name,literal_part", [
    ("image%23one.png", "image#one"),
    ("image%2523one.png", "image%23one"),
    ("image%2520one.png", "image%20one"),
])
def test_deep_scan_write_and_cache_registration_agree_on_literal_paths(downloader, tmp_path, monkeypatch, prefix, remote_name, literal_part):
    url = prefix + remote_name
    (tmp_path / "seed.css").write_text("body{}", "utf-8")
    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: {url})
    monkeypatch.setattr(image_pipeline, "fetch_response", lambda candidate, **_kw: response(candidate, b"PNG", "image/png"))
    monkeypatch.setattr(image_pipeline.runtime_state, "_HTTP2_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a: None)
    results = image_pipeline._deep_scan_and_download_assets(str(tmp_path), START, str(tmp_path), max_workers=1, ai_mode="off")
    assert list(results) == [url]
    saved = tmp_path / results[url]
    assert saved.is_file()
    assert literal_part in saved.name
    downloader._register_deep_scan_results(results)
    monkeypatch.setattr(downloader, "_fetch", Mock(side_effect=AssertionError("deep scan fetched twice")))
    assert downloader._download_asset(url) == str(saved)


@pytest.mark.skipif(os.environ.get("CYOA_RUNTIME_SMOKE") != "1", reason="optional live Chromium check")
def test_redirected_website_recovers_failed_route_and_navigates_offline(tmp_path, monkeypatch):
    import base64

    from playwright.sync_api import sync_playwright

    from cyoa_downloader_app.network import browser

    story = tmp_path / "game"
    chapter = story / "chapters/one"
    ending = chapter / "two"
    ending.mkdir(parents=True)
    (story / "index.html").write_text('<base href="chapters/"><a id="one" href="one">One</a>', "utf-8")
    (chapter / "index.html").write_text(
        '<link rel="stylesheet" href="style.css"><img id="hero" src="hero.png">'
        '<script src="app.js"></script><a id="two" href="two#end">Two</a>'
        '<img id="responsive" srcset="data:image/png;base64,AAAA 1x,hero.png?crop=1,2,3,4 2x">'
        '<svg width="10" height="10"><use id="icon" href="icons%23set%25.svg#mark"></use></svg>'
        '<iframe id="frame" src="frame/#chapter"></iframe>', "utf-8",
    )
    (chapter / "hero.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII="
    ))
    (chapter / "style.css").write_text("body { color: rgb(1, 2, 3); }", "utf-8")
    (chapter / "app.js").write_text("window.archiveFixture = true;", "utf-8")
    (chapter / "icons#set%.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><symbol id="mark" viewBox="0 0 10 10">'
        '<rect width="10" height="10" fill="red"/></symbol></svg>', "utf-8",
    )
    (chapter / "frame").mkdir()
    (chapter / "frame/index.html").write_text(
        '<h2 id="frame-heading">Offline frame</h2><img id="frame-hero" src="../hero.png">', "utf-8",
    )
    (ending / "index.html").write_text('<h1 id="end">Offline ending</h1>', "utf-8")
    failed_once = []
    offline = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.startswith("/game/") and offline:
                self.send_error(503)
                return
            if self.path.rstrip("/") == "/game/chapters/one/two" and not failed_once:
                failed_once.append(True)
                self.send_error(503)
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(website, "_load_settings", lambda: {})
    monkeypatch.setattr(browser, "_fetch_headless", lambda *_args: None)
    with requests.Session() as session:
        session.trust_env = False

        def fetch(_self, url):
            result = session.get(url, timeout=5, stream=True)
            if not result:
                result.close()
                return None
            return result

        monkeypatch.setattr(website.WebsiteDownloader, "_fetch", fetch)
        start = f"http://127.0.0.1:{server.server_port}/game"
        output = tmp_path / "archive"
        instance = website.WebsiteDownloader(start, str(output), archive_strategy="smart")
        try:
            instance._download_html(start)
            first = archive_runner.run_archive_extensions(instance, POLICY)
            assert len(first["pages"]) == 2
            assert len(first["route_failures"]) == 1
            resumed = archive_runner.resume_existing_archive(str(output), start, POLICY)
            assert len(resumed["pages"]) == 3
            assert resumed["route_failures"] == []
            assert instance.validate_integrity()["missing"] == []
            offline.append(True)
            with sync_playwright() as playwright:
                chromium = playwright.chromium.launch(headless=True)
                try:
                    page = chromium.new_page(device_scale_factor=2)
                    remote_requests = []
                    page.on("request", lambda request: remote_requests.append(request.url) if "/game/" in request.url else None)
                    page.goto(f"http://127.0.0.1:{server.server_port}/archive/index.html")
                    page.locator("#one").click()
                    page.wait_for_load_state("load")
                    assert page.locator("#hero").evaluate("img => img.complete && img.naturalWidth > 0")
                    assert page.locator("#responsive").evaluate(
                        "async img => { await img.decode(); return img.complete && !img.currentSrc.startsWith('data:'); }"
                    )
                    assert page.evaluate("window.archiveFixture") is True
                    assert page.locator("body").evaluate("node => getComputedStyle(node).color") == "rgb(1, 2, 3)"
                    page.wait_for_function("document.querySelector('#icon').getBBox().width === 10")
                    frame = page.frame_locator("#frame")
                    assert frame.locator("#frame-heading").inner_text() == "Offline frame"
                    assert frame.locator("#frame-hero").evaluate("img => img.complete && img.naturalWidth > 0")
                    page.locator("#two").click()
                    page.wait_for_load_state("load")
                    assert page.locator("#end").inner_text() == "Offline ending"
                    assert page.url.endswith("#end")
                    assert remote_requests == []
                finally:
                    chromium.close()
        finally:
            instance.close()
            instance.session.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
