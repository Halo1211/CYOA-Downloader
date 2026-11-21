"""Interrupted archive snapshots, conditional bodies and incremental scans."""

import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from cyoa_downloader_app.core import atomic_io, cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import archive_runner, website, website_recovery
from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download.route_crawler import RouteCrawler
from cyoa_downloader_app.storage.archive_checkpoint import ArchiveCheckpoint
from tools.run_live_queue import CatalogueSnapshot

START = "https://example.test/game/"
POLICY = ArchivePolicy(strategy="smart", max_pages=20)


def response(url, body=b"image-body", status=200, **headers):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body
    result._content_consumed = True
    result._cyoa_native_checkpoint = True
    result.headers.update({"Content-Type": "image/png", "Content-Length": str(len(body)), **headers})
    result.close = Mock()
    return result


@pytest.fixture
def factory(tmp_path, monkeypatch):
    instances = []
    context = ["profile-one"]
    monkeypatch.setattr(website, "_load_settings", lambda: {})
    monkeypatch.setattr(website.WebsiteDownloader, "_checkpoint_context", lambda _self: context[0])
    monkeypatch.setattr(website.WebsiteDownloader, "_download_runtime_template_assets", lambda *_a: None)
    monkeypatch.setattr(website, "_throttle_bandwidth", lambda *_a: None)
    monkeypatch.setattr(website, "vpn_requirement_satisfied", lambda: True)

    def create(resume=False):
        item = website.WebsiteDownloader(START, str(tmp_path), archive_strategy="smart")
        instances.append(item)
        if resume:
            item.enable_checkpoint_resume()
        return item

    yield create, context
    for item in instances:
        item.close()
        item.session.close()


def test_record_hash_and_generation_fence(tmp_path):
    local = tmp_path / "asset.png"
    local.write_bytes(b"complete")
    old = ArchiveCheckpoint(str(tmp_path), "one")
    assert old.put("asset", START, str(local))
    assert old.get("asset", START)["size"] == 8
    current = ArchiveCheckpoint(str(tmp_path), "one", resume=True)
    assert current.compatible
    assert not old.put("asset", START, str(local), stale=True)
    assert old.get("asset", START) is None
    assert "stale" not in current.get("asset", START)
    stamp = local.stat()
    local.write_bytes(b"corrupt!")
    os.utime(local, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert current.get("asset", START) is None
    changed = ArchiveCheckpoint(str(tmp_path), "two", resume=True)
    assert not changed.compatible
    assert changed.urls("asset") == []


@pytest.mark.parametrize("relative", ["../outside", "C:/outside", "file.part/../../outside"])
def test_unsafe_checkpoint_path_is_not_restored(tmp_path, relative):
    checkpoint = ArchiveCheckpoint(str(tmp_path), "one")
    with sqlite3.connect(checkpoint.path) as db:
        db.execute("INSERT INTO records VALUES (?,?,?)", ("asset", START, json.dumps({
            "local": relative, "size": 1, "sha256": "x",
        })))
    assert checkpoint.get("asset", START) is None


def test_conditional_resume_transfers_no_duplicate_body(factory, monkeypatch):
    create, _context = factory
    url = START + "hero.png?v=one"
    fetched = []

    def fetch(target, **kwargs):
        fetched.append(dict(kwargs["extra_headers"]))
        conditional = kwargs["extra_headers"].get("If-None-Match") == '"one"'
        return response(target, status=304 if conditional else 200, ETag='"one"')

    monkeypatch.setattr(website, "fetch_response", fetch)
    before = create()
    local = before.download_asset(url)
    original = Path(local).read_bytes()
    after = create(resume=True)
    assert after.download_asset(url) == local
    assert fetched[1]["If-None-Match"] == '"one"'
    assert Path(local).read_bytes() == original
    assert len(after._success_items) == 1


@pytest.mark.parametrize("headers", [
    {}, {"Vary": "Cookie", "ETag": '"one"'}, {"Set-Cookie": "auth=secret", "ETag": '"one"'},
    {"Content-Encoding": "gzip", "ETag": '"one"'}, {"Cache-Control": "no-store", "ETag": '"one"'},
    {"Content-Range": "bytes 0-9/20", "ETag": '"one"'},
])
def test_ineligible_metadata_uses_full_fetch(factory, monkeypatch, headers):
    create, _context = factory
    calls = []
    def fetch(url, **kwargs):
        calls.append(kwargs["extra_headers"])
        return response(url, **headers)
    monkeypatch.setattr(website, "fetch_response", fetch)
    create().download_asset(START + "hero.png")
    create(resume=True).download_asset(START + "hero.png")
    assert len(calls) == 2
    assert not any("If-None-Match" in item or "If-Modified-Since" in item for item in calls)


@pytest.mark.parametrize("mutation", ["context", "hash", "missing", "query"])
def test_changed_identity_never_sends_stale_validator(factory, monkeypatch, mutation):
    create, context = factory
    calls = []
    def fetch(url, **kwargs):
        calls.append(kwargs["extra_headers"])
        return response(url, ETag='"one"')
    monkeypatch.setattr(website, "fetch_response", fetch)
    local = Path(create().download_asset(START + "hero.png?v=one"))
    if mutation == "context":
        context[0] = "profile-two"
    elif mutation == "hash":
        local.write_bytes(b"corruption")
    elif mutation == "missing":
        local.unlink()
    url = START + ("hero.png?v=two" if mutation == "query" else "hero.png?v=one")
    create(resume=True).download_asset(url)
    assert "If-None-Match" not in calls[-1]


def test_last_modified_and_changed_remote_body(factory, monkeypatch):
    create, _context = factory
    calls = []
    def fetch(url, **kwargs):
        calls.append(kwargs["extra_headers"])
        return response(url, body=b"first" if len(calls) == 1 else b"second", **{"Last-Modified": "Sun, 15 Nov 2026 00:00:00 GMT"})
    monkeypatch.setattr(website, "fetch_response", fetch)
    local = create().download_asset(START + "hero.png")
    assert create(resume=True).download_asset(START + "hero.png") == local
    assert calls[1]["If-Modified-Since"].startswith("Sun,")
    assert Path(local).read_bytes() == b"second"


@pytest.mark.parametrize("failure", ["body", "disk", "cancel"])
def test_failed_body_never_commits_checkpoint(factory, monkeypatch, failure):
    create, _context = factory
    item = create()
    url = START + "hero.png"
    result = response(url, **{"Content-Length": "500"})
    monkeypatch.setattr(item, "_fetch", lambda _url: result)
    if failure == "disk":
        monkeypatch.setattr(atomic_io.os, "replace", Mock(side_effect=OSError("disk full")))
        result.headers["Content-Length"] = str(len(result.content))
    elif failure == "cancel":
        monkeypatch.setattr(website, "atomic_stream_response_to_file", Mock(side_effect=DownloadCancelledError()))
    with pytest.raises((OSError, DownloadCancelledError)):
        item.download_asset(url)
    checkpoint = item._archive_checkpoint()
    assert checkpoint.get("asset", url) is None
    assert not list(Path(item.output_folder).rglob("*.part"))
    assert not item._download_owners


def test_cancelled_checkpoint_hash_does_not_commit(tmp_path, monkeypatch):
    local = tmp_path / "asset"
    local.write_bytes(b"valid")
    item = ArchiveCheckpoint(str(tmp_path), "one")
    monkeypatch.setattr(cancellation, "_raise_if_cancelled", Mock(side_effect=DownloadCancelledError()))
    monkeypatch.setattr(item, "digest", Mock(side_effect=DownloadCancelledError()))
    with pytest.raises(DownloadCancelledError):
        item.put("asset", START, str(local))
    assert item.urls("asset") == []


def test_interrupted_route_crawl_resumes_without_manifest_or_repeated_assets(factory, monkeypatch, tmp_path):
    create, _context = factory
    calls = []
    interrupted = [True]
    def fetch(url, **kwargs):
        calls.append(url)
        if url == START + "next" and interrupted[0]:
            raise DownloadCancelledError()
        if url in {START, START + "next"}:
            return response(url, b'<img src="hero.png"><a href="next#end">Next</a>', **{"Content-Type": "text/html"})
        return response(url, ETag='"one"')
    monkeypatch.setattr(website, "fetch_response", fetch)
    with pytest.raises(DownloadCancelledError):
        RouteCrawler(create(), POLICY).crawl()
    assert not (tmp_path / "archive_manifest.json").exists()
    interrupted[0] = False
    calls.clear()
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert calls == [START + "next", START + "hero.png"]
    assert len(result["pages"]) == 2
    assert 'routes/next/index.html#end' in (tmp_path / "index.html").read_text("utf-8")
    calls.clear()
    archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert calls == []


@pytest.mark.parametrize("mutation", ["route", "dependency", "context"])
def test_corrupted_route_snapshot_is_refetched(factory, monkeypatch, tmp_path, mutation):
    create, context = factory
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return response(url, b'<img src="hero.png">' if url == START else b"image", **{
            "Content-Type": "text/html" if url == START else "image/png", "ETag": '"one"',
        })
    monkeypatch.setattr(website, "fetch_response", fetch)
    RouteCrawler(create(), POLICY).crawl()
    if mutation == "context":
        context[0] = "two"
    else:
        (tmp_path / ("index.html" if mutation == "route" else "hero.png")).write_bytes(b"broken")
    calls.clear()
    archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert START in calls
    assert (tmp_path / "hero.png").read_bytes() == b"image"


def test_text_scan_invalidates_on_file_source_and_dependency_mapping(factory, tmp_path, monkeypatch):
    create, _context = factory
    item = create()
    script = tmp_path / "app.js"
    script.write_text("const x=1;", "utf-8")
    process = Mock(side_effect=lambda text, *_a: text)
    monkeypatch.setattr(item, "_process_js", process)
    for _ in range(8):
        item.localize_existing_text_assets()
    assert process.call_count == 1
    script.write_text("const x=2;", "utf-8")
    item.localize_existing_text_assets()
    item._source_for_local[str(script)] = START + "other/"
    item.localize_existing_text_assets()
    (tmp_path / "new.png").write_bytes(b"image")
    item._downloaded[START + "new.png"] = str(tmp_path / "new.png")
    item.localize_existing_text_assets()
    assert process.call_count == 4


def test_catalog_snapshot_only_reads_changed_metadata(tmp_path, monkeypatch):
    paths = [tmp_path / f"{number}.json" for number in range(30)]
    for path in paths:
        path.write_text('{"status":"pending"}', "utf-8")
    reader = Path.read_text
    calls = []
    def read(path, *args, **kwargs):
        calls.append(path)
        return reader(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    snapshot = CatalogueSnapshot()
    for _ in range(3):
        for path in paths:
            assert snapshot.read(path, {})["status"] == "pending"
    assert len(calls) == 30
    paths[0].write_text('{"status":"downloaded"}', "utf-8")
    assert snapshot.read(paths[0], {})["status"] == "downloaded"
    paths[0].unlink()
    assert snapshot.read(paths[0], {"status": "pending"})["status"] == "pending"
    assert len(calls) == 31


def test_retry_assets_discovers_interrupted_archive_without_manifest(factory, monkeypatch, tmp_path):
    create, _context = factory
    item = create()
    item._archive_checkpoint()
    assert website_recovery.has_website_recovery_work(str(tmp_path))
    fetch = Mock(side_effect=lambda url, **_kw: response(url, b"<h1>Recovered</h1>", **{"Content-Type": "text/html"}))
    monkeypatch.setattr(website, "fetch_response", fetch)
    summary = website_recovery.retry_website_assets(str(tmp_path), POLICY)
    assert summary.archives_resumed == 1
    assert (tmp_path / "archive_manifest.json").is_file()
    assert not website_recovery.has_website_recovery_work(str(tmp_path))


def test_request_context_covers_cookies_settings_headers_and_source(tmp_path, monkeypatch):
    from cyoa_downloader_app.storage import history

    shared = requests.Session()
    settings = {"archive_strategy": "smart"}
    monkeypatch.setattr(history, "_metadata_context", lambda _url: (shared, "transport-profile"))
    monkeypatch.setattr(website, "_load_settings", lambda: settings.copy())
    item = website.WebsiteDownloader(START, str(tmp_path), archive_strategy="smart")
    try:
        initial = item._checkpoint_context()
        shared.cookies.set("auth", "secret", domain="example.test", path="/")
        cookie_context = item._checkpoint_context()
        assert cookie_context != initial
        assert "secret" not in cookie_context
        item.session.headers["User-Agent"] = "different"
        headers_context = item._checkpoint_context()
        assert headers_context != cookie_context
        settings["archive_strategy"] = "browser"
        settings_context = item._checkpoint_context()
        assert settings_context != headers_context
        item.start_url += "?choice=two"
        assert item._checkpoint_context() != settings_context
    finally:
        item.close()
        item.session.close()
        shared.close()


@pytest.mark.parametrize("invalid_304", ["validator", "context", "hash", "vary", "encoding", "no_store"])
def test_invalid_304_refetches_complete_body(factory, monkeypatch, invalid_304):
    create, context = factory
    calls = []
    local = [None]
    def fetch(url, **kwargs):
        calls.append(kwargs["extra_headers"])
        if len(calls) == 2:
            if invalid_304 == "context":
                context[0] = "changed-during-request"
            elif invalid_304 == "hash":
                local[0].write_bytes(b"modified")
            headers = {"vary": {"Vary": "Cookie"}, "encoding": {"Content-Encoding": "gzip"},
                       "no_store": {"Cache-Control": "no-store"}}.get(invalid_304, {})
            return response(url, status=304, ETag='"different"' if invalid_304 == "validator" else '"one"', **headers)
        return response(url, ETag='"one"')
    monkeypatch.setattr(website, "fetch_response", fetch)
    local[0] = Path(create().download_asset(START + "hero.png"))
    assert create(resume=True).download_asset(START + "hero.png")
    assert len(calls) == 3
    assert "If-None-Match" not in calls[-1]
    assert local[0].read_bytes() == b"image-body"


@pytest.mark.parametrize("validator", ["x" * 4097, '"one"\r\nInjected: true'])
def test_oversized_or_invalid_validator_is_never_sent(factory, monkeypatch, validator):
    create, _context = factory
    calls = []
    def fetch(url, **kwargs):
        calls.append(kwargs["extra_headers"])
        return response(url, ETag=validator)
    monkeypatch.setattr(website, "fetch_response", fetch)
    create().download_asset(START + "hero.png")
    create(resume=True).download_asset(START + "hero.png")
    assert "If-None-Match" not in calls[-1]


def test_busy_checkpoint_keeps_previous_transaction_and_download_output(factory, monkeypatch, tmp_path):
    create, _context = factory
    item = create()
    checkpoint = item._archive_checkpoint()
    saved = tmp_path / "saved.png"
    saved.write_bytes(b"previous")
    checkpoint.put("asset", START + "saved.png", str(saved))
    monkeypatch.setattr(website, "fetch_response", lambda url, **_kw: response(url, ETag='"one"'))
    with sqlite3.connect(checkpoint.path) as locked:
        locked.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        result = item.download_asset(START + "new.png")
        assert time.monotonic() - started < 1
        assert Path(result).read_bytes() == b"image-body"
    assert checkpoint.get("asset", START + "new.png") is None
    assert checkpoint.get("asset", START + "saved.png")["path"] == str(saved)


@pytest.mark.parametrize("backend", ["off", "cloudscraper"])
def test_only_native_request_boundary_certifies_checkpoint_provenance(monkeypatch, backend):
    from cyoa_downloader_app.network import fetch_base

    result = response(START)
    result._cyoa_native_checkpoint = False
    session = SimpleNamespace(get=lambda *_a, **_kw: result)
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(_CLOUDFLARE_MODE=backend, logger=Mock()))
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **_kw: session)
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda *_a: None)
    monkeypatch.setattr(fetch_base, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda *_a: False)
    monkeypatch.setattr(fetch_base, "is_cloudflare_challenge", lambda *_a: False)
    monkeypatch.setattr(fetch_base, "get_headers_for_url", lambda *_a: {})
    assert fetch_base.base_fetch_response(START, stream=True) is result
    assert result._cyoa_native_checkpoint == (backend == "off")


def test_unknown_backend_does_not_reuse_validators(factory, monkeypatch):
    create, _context = factory
    calls = []
    def fetch(url, **kwargs):
        calls.append(kwargs["extra_headers"])
        result = response(url, ETag='"one"')
        del result._cyoa_native_checkpoint
        return result
    monkeypatch.setattr(website, "fetch_response", fetch)
    create().download_asset(START + "hero.png")
    create(resume=True).download_asset(START + "hero.png")
    assert "If-None-Match" not in calls[-1]


def test_unavailable_optional_context_keeps_normal_download(factory, monkeypatch):
    create, _context = factory
    item = create()
    monkeypatch.setattr(item, "_checkpoint_context", Mock(side_effect=KeyError("optional profile unavailable")))
    monkeypatch.setattr(website, "fetch_response", lambda url, **_kw: response(url))
    assert Path(item.download_asset(START + "hero.png")).read_bytes() == b"image-body"
    assert item._checkpoint_disabled


def test_corrupt_checkpoint_never_downgrades_to_presence_only_resume(factory, monkeypatch, tmp_path):
    _create, _context = factory
    (tmp_path / "_audit").mkdir()
    (tmp_path / "_audit" / "archive-checkpoints.sqlite3").write_bytes(b"broken database")
    (tmp_path / "index.html").write_text("stale local content", "utf-8")
    (tmp_path / "archive_manifest.json").write_text(json.dumps({
        "pages": [{"url": START, "local": "index.html"}],
    }), "utf-8")
    fetch = Mock(side_effect=lambda url, **_kw: response(url, b"<h1>Refetched</h1>", **{"Content-Type": "text/html"}))
    monkeypatch.setattr(website, "fetch_response", fetch)
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    assert fetch.call_count == 1
    assert len(result["pages"]) == 1
    assert "Refetched" in (tmp_path / "index.html").read_text("utf-8")


def test_restored_route_reserves_path_before_new_colliding_route(factory, monkeypatch, tmp_path):
    create, _context = factory
    def fetch(url, **_kwargs):
        body = {START: b'<a href="a%3Fb">One</a><a href="a_b">Two</a>',
                START + "a%3Fb": b"<h1>First page</h1>", START + "a_b": b"<h1>Second page</h1>"}[url]
        return response(url, body, **{"Content-Type": "text/html"})
    monkeypatch.setattr(website, "fetch_response", fetch)
    RouteCrawler(create(), ArchivePolicy(strategy="smart", max_pages=2)).crawl()
    result = archive_runner.resume_existing_archive(str(tmp_path), START, POLICY)
    pages = {item["url"]: item["local"] for item in result["pages"]}
    assert pages[START + "a%3Fb"] != pages[START + "a_b"]
    assert "First page" in (tmp_path / pages[START + "a%3Fb"]).read_text("utf-8")
    assert "Second page" in (tmp_path / pages[START + "a_b"]).read_text("utf-8")
