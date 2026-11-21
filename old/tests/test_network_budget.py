"""Shared HTTP limits must follow response lifetime across independent clients."""

import gc
import gzip
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.network import sessions
from cyoa_downloader_app.network.budget import RequestBudget, request_scope


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "Fixture timed out"
        time.sleep(.002)


def test_host_quota_does_not_occupy_global_slots_or_block_another_host():
    budget = RequestBudget(total=2, per_host=1)
    first = budget.acquire("https://HOST.test:443/")
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(budget.acquire, "http://host.test.:8080/other")
        try:
            wait_for(lambda: budget.snapshot()["waiting"] == 1)
            with budget.acquire("https://other.test/"):
                assert budget.snapshot()["active"] == 2
                assert not waiting.done()
            first.release()
            waiting.result(2).release()
        finally:
            first.release()
    assert budget.snapshot()["active"] == 0 and budget.snapshot()["hosts"] == {}


def test_waiting_background_request_can_cancel_without_touching_active_lease():
    budget = RequestBudget(total=1, per_host=1)
    held = budget.acquire("https://one.test/")
    cancel = threading.Event()
    def request():
        with request_scope(priority=20, cancel_event=cancel):
            return budget.acquire("https://two.test/")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(request)
            wait_for(lambda: budget.snapshot()["waiting"] == 1)
            started = time.monotonic()
            cancel.set()
            with pytest.raises(DownloadCancelledError):
                pending.result(1)
            assert time.monotonic() - started < .3
        assert budget.snapshot()["active"] == 1 and not budget.snapshot()["waiting"]
    finally:
        held.release()


def test_foreground_admission_precedes_waiting_history_and_scope_restores():
    budget = RequestBudget(total=1, per_host=1)
    held = budget.acquire("https://host.test/")
    order = []
    def request(label, priority):
        with request_scope(priority=priority), budget.acquire("https://host.test/"):
            order.append(label)
    with ThreadPoolExecutor(max_workers=2) as pool:
        background = pool.submit(request, "history", 20)
        try:
            wait_for(lambda: budget.snapshot()["waiting"] == 1)
            foreground = pool.submit(request, "download", 10)
            wait_for(lambda: budget.snapshot()["waiting"] == 2)
            held.release()
            foreground.result(2)
            background.result(2)
        finally:
            held.release()
    assert order == ["download", "history"]
    cancelled = threading.Event()
    cancelled.set()
    with request_scope(cancel_event=cancelled), pytest.raises(DownloadCancelledError):
        budget.acquire("https://host.test/")
    with budget.acquire("https://host.test/"):
        assert budget.snapshot()["active"] == 1


@pytest.mark.parametrize("mode", ["content", "stream", "partial", "close", "gc"])
def test_response_lifetime_releases_exactly_once(mode):
    budget = RequestBudget(total=1, per_host=1)
    response = requests.Response()
    response.raw = io.BytesIO(b"abcdefgh")
    response.status_code = 200
    sessions._lease_response(response, budget.acquire("https://host.test/"))
    assert budget.snapshot()["active"] == 1
    if mode == "content":
        assert response.content == b"abcdefgh"
        assert response.content == b"abcdefgh"
    elif mode == "stream":
        assert b"".join(response.iter_content(2)) == b"abcdefgh"
    elif mode == "partial":
        chunks = response.iter_content(2)
        assert next(chunks) == b"ab"
        chunks.close()
        assert response.raw.closed
    elif mode == "gc":
        del response
        gc.collect()
    else:
        response.close()
    assert budget.snapshot()["active"] == 0
    if mode != "gc":
        response.close()
        response.close()
    assert budget.snapshot()["active"] == 0


def test_transport_exception_releases_lease(monkeypatch):
    budget = RequestBudget(total=1, per_host=1)
    monkeypatch.setattr(sessions, "HTTP_BUDGET", budget)
    def fail(*_, **__):
        raise requests.Timeout("fixture")
    monkeypatch.setattr(sessions.HTTPAdapter, "send", fail)
    with sessions.create_retry_session() as session, pytest.raises(requests.Timeout):
        session.get("https://example.invalid/")
    assert budget.snapshot()["active"] == 0


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/gzip")
                body = b"redirect"
            elif self.path == "/truncated":
                self.send_response(200)
                body = b"short"
                self.send_header("Content-Length", "99")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
                return
            else:
                self.send_response(200)
                body = gzip.compress("payload α".encode()) if self.path == "/gzip" else b"payload"
                if self.path == "/gzip":
                    self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{fixture.server_port}"
    finally:
        fixture.shutdown()
        fixture.server_close()
        thread.join(2)


def test_independent_sessions_share_stream_body_quota_and_reset_does_not_bypass(server, monkeypatch):
    budget = RequestBudget(total=1, per_host=1)
    monkeypatch.setattr(sessions, "HTTP_BUDGET", budget)
    first = sessions.create_retry_session()
    second = sessions.create_retry_session()
    first.trust_env = second.trust_env = False
    response = first.get(server, stream=True, timeout=2)
    first.close()  # Closing a connection pool is not consuming its active body.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(second.get, server, timeout=2)
        try:
            wait_for(lambda: budget.snapshot()["waiting"] == 1)
            assert not pending.done()
            assert response.content == b"payload"
            with pending.result(2) as result:
                assert result.content == b"payload"
        finally:
            response.close()
            second.close()
    assert budget.snapshot()["active"] == budget.snapshot()["waiting"] == 0


def test_redirect_gzip_and_truncated_body_do_not_leak_or_deadlock(server, monkeypatch):
    budget = RequestBudget(total=1, per_host=1)
    monkeypatch.setattr(sessions, "HTTP_BUDGET", budget)
    with sessions.create_retry_session() as session:
        session.trust_env = False
        with session.get(server + "/redirect", stream=True, timeout=2) as response:
            assert response.history[0].content == b"redirect"
            assert response.content.decode() == "payload α"
        assert budget.snapshot()["active"] == 0
        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            session.get(server + "/truncated", timeout=2)
        assert budget.snapshot()["active"] == 0
