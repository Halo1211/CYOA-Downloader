"""Runtime reuse is bounded, temporary, exact-URL and safety-path preserving."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import website
from cyoa_downloader_app.network import captured_responses
from cyoa_downloader_app.network.captured_responses import CapturedResponseStore, RuntimeCaptureStorageError


def response(url="https://example.test/image.png", body=b"image payload", *, headers=None, method="GET", status=200):
    calls = []
    def read():
        calls.append(1)
        return body
    return SimpleNamespace(url=url, headers={"content-type": "image/png", "content-length": str(len(body)), **(headers or {})},
                           status=status, request=SimpleNamespace(method=method), body=read, calls=calls)


@pytest.mark.parametrize("options", [
    {"headers": {"content-length": ""}}, {"headers": {"content-length": "bogus"}},
    {"headers": {"content-length": "-1"}}, {"headers": {"content-length": "999999999"}},
    {"headers": {"content-encoding": "gzip"}}, {"headers": {"content-range": "bytes 0-9/20"}},
    {"status": 206}, {"status": 304}, {"method": "POST"}, {"url": "file:///image.png"},
])
def test_ineligible_response_never_reads_browser_body(tmp_path, options):
    candidate = response(**options)
    with CapturedResponseStore(tmp_path) as store:
        assert not store.capture(candidate)
        assert not candidate.calls and not store.urls
    assert not list(tmp_path.iterdir())


def test_quotas_exact_query_identity_and_declared_length_mismatch(tmp_path):
    with CapturedResponseStore(tmp_path, max_body_bytes=4, max_total_bytes=6, max_entries=2) as store:
        first = response("https://example.test/a?id=1", b"one")
        second = response("https://example.test/a?id=2", b"two")
        assert store.capture(first) and store.capture(second)
        extra = response("https://example.test/b", b"x")
        assert not store.capture(extra) and not extra.calls
        assert store.captured_bytes == 6 and store.captured_count == 2
        for url, expected in ((first.url, b"one"), (second.url, b"two")):
            with store.take(url) as saved:
                assert b"".join(saved.iter_content(2)) == expected
        assert store.take(first.url) is None and store.reused_count == 2 and store.reused_bytes == 6
    liar = response(body=b"much larger than declared", headers={"content-length": "1"})
    with CapturedResponseStore(tmp_path, max_body_bytes=4) as store:
        assert not store.capture(liar) and liar.calls and not store.urls


def test_duplicate_and_nested_body_rpc_do_not_stack(tmp_path):
    with CapturedResponseStore(tmp_path) as store:
        nested = response("https://example.test/nested.png")
        first = response()
        def read():
            assert not store.capture(nested) and not nested.calls
            return b"image payload"
        first.body = read
        assert store.capture(first)
        duplicate = response()
        assert not store.capture(duplicate) and not duplicate.calls
        assert store.capture(nested)


def test_outstanding_response_and_unused_files_close_with_scope(tmp_path):
    store = CapturedResponseStore(tmp_path)
    assert store.capture(response())
    saved = store.take("https://example.test/image.png")
    assert not saved._content_consumed and not saved.raw.closed
    assert store.capture(response("https://example.test/unused.png"))
    store.close()
    store.close()
    assert saved.raw.closed and not list(tmp_path.iterdir())
    saved.close()


def test_disk_failure_is_raised_outside_browser_error_boundary(tmp_path, monkeypatch):
    def fail(*a, **kw):
        raise OSError("fixture disk full")
    monkeypatch.setattr(captured_responses, "spool_chunks", fail)
    with CapturedResponseStore(tmp_path) as store:
        assert not store.capture(response())
        with pytest.raises(RuntimeCaptureStorageError, match="fixture disk full"):
            store.raise_if_failed()
    assert not list(tmp_path.iterdir())


def test_cancellation_after_body_leaves_no_capture_file(tmp_path):
    cancel = threading.Event()
    cancellation.set_progress_event_sink(None, cancel)
    candidate = response()
    def read():
        cancel.set()
        return b"image payload"
    candidate.body = read
    try:
        with CapturedResponseStore(tmp_path) as store, pytest.raises(DownloadCancelledError):
            store.capture(candidate)
    finally:
        cancellation.clear_progress_event_sink()
    assert not list(tmp_path.iterdir())


@pytest.fixture
def downloader(tmp_path, monkeypatch):
    monkeypatch.setattr(website, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(website, "_ssrf_block_cross_origin", lambda *a: False)
    monkeypatch.setattr(website, "fetch_response", lambda *a, **kw: pytest.fail("Captured asset was fetched again"))
    instance = website.WebsiteDownloader("https://example.test/", str(tmp_path / "output"), archive_strategy="auto")
    try:
        yield instance
    finally:
        instance.close()


def test_capture_repairs_previous_failure_and_restores_downloader_scope(downloader, tmp_path):
    candidate = response()
    downloader._downloaded[candidate.url] = None
    downloader._failed_items.append({"url": candidate.url, "error": "HTTP 403"})
    with CapturedResponseStore(tmp_path) as store:
        assert store.capture(candidate) and downloader.needs_runtime_response(candidate.url)
        with downloader.use_runtime_responses(store):
            saved = downloader.download_asset(candidate.url)
            assert Path(saved).read_bytes() == b"image payload"
        assert not downloader._failed_items and not downloader.needs_runtime_response(candidate.url)
        assert downloader._captured_runtime_responses is None and store.reused_count == 1


def test_css_dependencies_and_unicode_use_normal_rewriters(downloader, tmp_path):
    css = response("https://example.test/style.css", '/* 雪 */body{background:url(pic.svg)}'.encode(),
                   headers={"content-type": "text/css"})
    svg = response("https://example.test/pic.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                   headers={"content-type": "image/svg+xml"})
    with CapturedResponseStore(tmp_path) as store:
        assert store.capture(css) and store.capture(svg)
        with downloader.use_runtime_responses(store):
            local = downloader.download_asset(css.url, preferred_kind="css")
        assert "雪" in Path(local).read_text("utf-8") and store.reused_count == 2
        assert Path(downloader._downloaded[svg.url]).read_bytes() == svg.body()


def test_cached_response_does_not_bypass_ssrf_or_vpn(downloader, tmp_path, monkeypatch):
    candidate = response("http://127.0.0.1/private.png")
    with CapturedResponseStore(tmp_path) as store:
        assert store.capture(candidate)
        with downloader.use_runtime_responses(store):
            monkeypatch.setattr(website, "_ssrf_block_cross_origin", lambda *a: True)
            assert not downloader.needs_runtime_response(candidate.url)
            assert downloader.download_asset(candidate.url) is None and store.reused_count == 0
            monkeypatch.setattr(website, "vpn_requirement_satisfied", lambda: False)
            assert not downloader.needs_runtime_response(candidate.url)
            assert downloader._fetch(candidate.url) is None and store.reused_count == 0


def test_phase_release_allows_later_browser_but_final_close_prevents_it(downloader, monkeypatch):
    closed = []
    monkeypatch.setattr(website, "BrowserFetchSession", lambda: SimpleNamespace(close=lambda: closed.append(1)))
    first = downloader._get_browser_transport()
    downloader.release_browser_transport()
    assert closed == [1] and downloader._get_browser_transport() is not first
    downloader.close()
    assert closed == [1, 1] and downloader._get_browser_transport() is None
