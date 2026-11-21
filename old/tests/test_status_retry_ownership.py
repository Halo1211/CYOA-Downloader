"""Native media status retries have one owner without mutating shared sessions."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from urllib3.exceptions import ConnectTimeoutError

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import image_pipeline as pipeline
from cyoa_downloader_app.network import fetch_base, sessions
from cyoa_downloader_app.network.budget import HTTP_BUDGET
from cyoa_downloader_app.network.retry_policy import (
    caller_status_retry_scope,
    retry_after_seconds,
    status_retries_owned_by_caller,
)
from cyoa_downloader_app.runtime import state, surface


@pytest.fixture
def server():
    counts = {}
    body = b"\x89PNG\r\n\x1a\n" + b"payload" * 100

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            counts[self.path] = counts.get(self.path, 0) + 1
            args = parse_qs(urlparse(self.path).query)
            recover_at = int(args.get("recover", ["1000"])[0])
            status = int(args.get("status", ["503"])[0]) if counts[self.path] < recover_at else 200
            payload = body if status == 200 else b"failure"
            self.send_response(status)
            self.send_header("Content-Type", "image/png" if status == 200 else "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            if "after" in args:
                self.send_header("Retry-After", args["after"][0])
            self.end_headers()
            self.wfile.write(payload)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", counts, body
    finally:
        http.shutdown()
        http.server_close()
        thread.join(2)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_shared_session_status_policy_is_scoped_per_caller(monkeypatch, server, status):
    base, counts, body = server
    monkeypatch.setattr(state, "_proxy_mode", "disabled")
    monkeypatch.setattr(sessions, "_cancel_aware_sleep", lambda *_: None)
    with sessions.create_retry_session() as client:
        retry = client.get_adapter(base).max_retries
        def request(scoped):
            path = f"/image.png?status={status}&recover=4&scoped={scoped}"
            if scoped:
                with caller_status_retry_scope():
                    response = client.get(base + path, stream=True)
            else:
                response = client.get(base + path, stream=True)
            try:
                assert response.status_code == (status if scoped else 200)
                assert response.content == (b"failure" if scoped else body)
                return path
            finally:
                response.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            paths = list(workers.map(request, (True, False)))
        assert [counts[path] for path in paths] == [1, 4]
        assert retry is client.get_adapter(base).max_retries and retry.total == 3
    assert HTTP_BUDGET.snapshot()["active"] == 0


def test_scope_restores_after_nested_cancellation_and_does_not_change_connect_retries():
    retry = sessions._CancellableRetry(total=3, connect=3, read=3)
    assert not status_retries_owned_by_caller()
    with caller_status_retry_scope():
        with pytest.raises(DownloadCancelledError), caller_status_retry_scope():
            raise DownloadCancelledError("fixture")
        assert status_retries_owned_by_caller()
        following = retry.increment(method="GET", url="/", error=ConnectTimeoutError("fixture"))
        assert following.total == following.connect == 2
        assert retry.is_retry("GET", 413, has_retry_after=True)
    assert not status_retries_owned_by_caller() and retry.total == 3


def test_auth_failure_still_tries_browser_cookie_profiles(monkeypatch, tmp_path):
    from test_media_streaming_optimization import StreamingResponse

    body = b"\x89PNG\r\n\x1a\n" + b"authenticated" * 100
    normal, profiles, responses = [], [], []
    def fetch(*a, **kw):
        normal.append(1)
        response = StreamingResponse(b"", status=403)
        responses.append(response)
        return response
    def cookies(profile):
        profiles.append(profile)
        def get(*a, **kw):
            response = StreamingResponse(body if profile == "firefox" else b"", status=200 if profile == "firefox" else 403)
            responses.append(response)
            return response
        return SimpleNamespace(get=get)
    for name, value in (
        ("fetch_response", fetch), ("_make_cookie_session", cookies),
        ("_cache_get_file", lambda *a: None), ("_cache_put_file", lambda *a: None),
        ("_SELENIUM_ENABLED", False), ("_is_gallery_dl_site", lambda *a: ""),
        ("_domain_throttle", lambda *a: None), ("_domain_record_success", lambda *a: None),
        ("_ssrf_block_cross_origin", lambda *a: False),
    ):
        monkeypatch.setattr(pipeline, name, value)
    _, localized, resolved = pipeline.process_images(json.dumps({"image": "protected.png"}), "https://example.test/",
        download=True, temp_folder=str(tmp_path), max_workers=1)
    assert len(normal) == 2 and profiles == ["chrome", "edge", "firefox"]
    assert (tmp_path / json.loads(localized)["image"]).read_bytes() == body and resolved
    assert all(response.closed for response in responses)


@pytest.mark.parametrize("value,expected", [(None, 0), ("garbage", 0), ("nan", 0), ("inf", 0),
                                            ("-10", 0), ("2.5", 2.5), ("8", 8)])
def test_retry_after_invalid_and_delta_values(value, expected):
    assert retry_after_seconds(value, now=1000) == expected


def test_retry_after_http_dates_and_past_dates():
    assert retry_after_seconds(formatdate(1120, usegmt=True), now=1000) == 120
    assert retry_after_seconds(formatdate(900, usegmt=True), now=1000) == 0


@pytest.fixture
def media(monkeypatch):
    for target, name, value in (
        (pipeline, "_cache_get_file", lambda *a: None),
        (pipeline, "_cache_put_file", lambda *a: None),
        (pipeline, "_SELENIUM_ENABLED", False),
        (pipeline, "_is_gallery_dl_site", lambda *a: ""),
        (pipeline, "_domain_record_failure", lambda *a: 0),
        (pipeline, "_domain_record_success", lambda *a: None),
        (pipeline, "_make_cookie_session", lambda *a: pytest.fail("Cookie extraction for a transient status")),
        (pipeline, "_cancel_aware_sleep", lambda *a: None),
        (fetch_base, "_domain_throttle", lambda *a: None),
        (state, "_proxy_mode", "disabled"),
        (surface, "_CLOUDFLARE_MODE", "off"),
    ):
        monkeypatch.setattr(target, name, value)
    sessions._v465_reset_shared_sessions()
    yield
    sessions._v465_reset_shared_sessions()


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("recover", [4, 1000])
def test_real_media_status_retry_count_and_saved_bytes(media, server, monkeypatch, tmp_path, status, recover):
    base, counts, body = server
    path = f"/image.png?status={status}&recover={recover}&after=2"
    def transport_sleep(*_):
        pytest.fail("Transport repeated a status owned by the media loop")
    monkeypatch.setattr(sessions, "_cancel_aware_sleep", transport_sleep)
    delays = []
    def asset_sleep(delay):
        assert HTTP_BUDGET.snapshot()["active"] == 0
        delays.append(delay)
    monkeypatch.setattr(pipeline, "_cancel_aware_sleep", asset_sleep)
    _, localized, resolved = pipeline.process_images(json.dumps({"image": path}), base,
        download=True, temp_folder=str(tmp_path), max_workers=1, wait_time=1)
    assert counts[path] == 4 and delays == [2, 2, 2]
    target = json.loads(localized)["image"]
    if recover == 4:
        assert (tmp_path / target).read_bytes() == body and resolved
    else:
        assert target == path and not resolved
        assert f"HTTP {status}" in (tmp_path / "failed_images.txt").read_text(encoding="utf-8")
    assert HTTP_BUDGET.snapshot()["active"] == 0
    assert not list(tmp_path.rglob("*.part")) and not list(tmp_path.rglob("*.asset"))


def test_cancel_real_media_backoff_cleans_scope_response_and_spool(media, server, monkeypatch, tmp_path):
    base, counts, _ = server
    cancel = threading.Event()
    cancellation.set_progress_event_sink(None, cancel)
    def sleep(delay):
        assert HTTP_BUDGET.snapshot()["active"] == 0
        cancel.set()
        cancellation._cancel_aware_sleep(delay)
    monkeypatch.setattr(pipeline, "_cancel_aware_sleep", sleep)
    try:
        with pytest.raises(DownloadCancelledError):
            pipeline.process_images(json.dumps({"image": "/image.png?status=429&after=60"}), base,
                download=True, temp_folder=str(tmp_path), max_workers=1)
    finally:
        cancellation.clear_progress_event_sink()
    assert sum(counts.values()) == 1 and HTTP_BUDGET.snapshot()["active"] == 0
    assert not status_retries_owned_by_caller() and not list(tmp_path.glob(".cyoa-media-*"))


def test_live_headless_switch_disables_and_reenables_fallback(media, monkeypatch, tmp_path):
    from test_media_streaming_optimization import StreamingResponse

    body = b"\x89PNG\r\n\x1a\n" + b"fallback" * 100
    browser_calls = []
    monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: StreamingResponse(b"", status=429))
    monkeypatch.setattr(pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(pipeline, "_ssrf_block_cross_origin", lambda *a: False)
    def headless(*a, **kw):
        browser_calls.append(1)
        return body
    monkeypatch.setattr(pipeline, "_fetch_headless", headless)
    monkeypatch.setattr(state, "_SELENIUM_ENABLED", False)
    _, original, resolved = pipeline.process_images(json.dumps({"image": "a.png"}), "https://example.test/",
        download=True, temp_folder=str(tmp_path / "off"), max_workers=1)
    assert not browser_calls and not resolved and json.loads(original)["image"] == "a.png"
    monkeypatch.setattr(state, "_SELENIUM_ENABLED", True)
    _, localized, resolved = pipeline.process_images(json.dumps({"image": "a.png"}), "https://example.test/",
        download=True, temp_folder=str(tmp_path / "on"), max_workers=1)
    assert browser_calls == [1] and resolved
    assert (tmp_path / "on" / json.loads(localized)["image"]).read_bytes() == body
