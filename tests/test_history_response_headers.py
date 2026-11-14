"""History reuses only completed, matching native representation headers."""

import gc
import gzip
import json
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from cyoa_downloader_app.network.response_metadata import (
    ResponseMetadataCapture,
    forget_response_metadata,
    observe_response_metadata,
)
from cyoa_downloader_app.storage import history
from tools.benchmark_auto_preparation import queue_fixture

URL = "https://example.test/source?q=1"


def response(url=URL):
    value = requests.Response()
    value.url, value.status_code = url, 200
    value.request = requests.Request("GET", url).prepare()
    value.headers.update({"ETag": '"v1"', "Content-Length": "4"})
    value._content, value._content_consumed = b"body", True
    return value


def test_snapshot_does_not_retain_body_and_is_consumed_once():
    session = object()
    capture = ResponseMetadataCapture(URL, lambda url: (session, "same"))
    with capture:
        value = response()
        ref = weakref.ref(value)
        observe_response_metadata(URL, value, session, stream=False)
        del value
        gc.collect()
        assert ref() is None
    assert capture.take(URL) == {"etag": '"v1"', "last_modified": "", "content_length": "4"}
    assert capture.take(URL) is None


@pytest.mark.parametrize("case", [
    "stream", "compressed", "range", "partial", "vary", "cookie", "no_store", "conditional",
    "redirect", "query", "method", "truncated", "unconsumed", "error", "oversized_header", "session",
])
def test_unsafe_representations_keep_probe_fallback(case):
    session = object()
    with ResponseMetadataCapture(URL, lambda url: (session, "same")) as capture:
        value = response()
        if case == "compressed": value.headers["Content-Encoding"] = "gzip"
        if case == "range": value.request.headers["Range"] = "bytes=0-0"
        if case == "partial": value.headers["Content-Range"] = "bytes 0-3/90"
        if case == "vary": value.headers["Vary"] = "Accept-Encoding"
        if case == "cookie": value.headers["Set-Cookie"] = "account=changed"
        if case == "no_store": value.headers["Cache-Control"] = "private, no-store"
        if case == "conditional": value.request.headers["If-None-Match"] = '"v1"'
        if case == "redirect": value.url = "https://other.test/source?q=1"
        if case == "query": value.url = URL + "2"
        if case == "method": value.request.method = "POST"
        if case == "truncated": value._content = b"x"
        if case == "unconsumed": value._content_consumed = False
        if case == "error": value.status_code = 503
        if case == "oversized_header": value.headers["ETag"] = "x" * 4097
        observe_response_metadata(URL, value, object() if case == "session" else session, stream=case == "stream")
    assert capture.take(URL) is None


@pytest.mark.parametrize("change", ["profile", "session", "context_error"])
def test_changed_context_is_discarded(change):
    current = [object(), "same"]
    def context(url):
        if current[1] == "error": raise KeyError("optional context")
        return tuple(current)
    with ResponseMetadataCapture(URL, context) as capture:
        observe_response_metadata(URL, response(), current[0], stream=False)
    if change == "profile": current[1] = "changed"
    if change == "session": current[0] = object()
    if change == "context_error": current[1] = "error"
    assert capture.take(URL) is None


def test_threads_and_nested_jobs_cannot_replace_parent_source():
    session = object()
    with ResponseMetadataCapture(URL, lambda url: (session, "same")) as outer:
        observe_response_metadata(URL, response(), session, stream=False)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(forget_response_metadata, URL).result()
        with ResponseMetadataCapture(URL + "2", lambda url: (session, "same")) as inner:
            observe_response_metadata(URL, response(), session, stream=False)
        assert inner.take(URL + "2") is None
    assert outer.take(URL)["etag"] == '"v1"'


def test_context_changed_during_download_is_not_a_matching_baseline():
    current = [object(), "before"]
    with ResponseMetadataCapture(URL, lambda url: tuple(current)) as capture:
        current[1] = "after"
        observe_response_metadata(URL, response(), current[0], stream=False)
    assert capture.take(URL) is None


def test_latest_failed_attempt_and_exception_cannot_leave_old_headers():
    session = object()
    with ResponseMetadataCapture(URL, lambda url: (session, "same")) as capture:
        observe_response_metadata(URL, response(), session, stream=False)
        forget_response_metadata(URL)
    assert capture.take(URL) is None
    with pytest.raises(OSError), capture:
        observe_response_metadata(URL, response(), session, stream=False)
        raise OSError("download failed")
    assert capture.take(URL) is None


def test_zero_length_is_valid_but_missing_baseline_is_not():
    session = object()
    with ResponseMetadataCapture(URL, lambda url: (session, "same")) as capture:
        value = response()
        value._content = b""
        value.headers = requests.structures.CaseInsensitiveDict({"Content-Length": "0"})
        observe_response_metadata(URL, value, session, stream=False)
    assert capture.take(URL)["content_length"] == "0"
    with capture:
        value.headers.clear()
        observe_response_metadata(URL, value, session, stream=False)
    assert capture.take(URL) is None


def test_active_gui_records_matching_headers_without_a_probe(monkeypatch, tmp_path):
    from cyoa_downloader_app.gui import final_behaviors as gui
    session = object()
    context = lambda url: (session, "same")
    monkeypatch.setattr(gui, "_metadata_context", context)
    monkeypatch.setattr(history, "_metadata_context", context)
    path = tmp_path / "history.json"
    monkeypatch.setattr(history, "_HISTORY_FILE", str(path))
    monkeypatch.setattr(history, "_defer_metadata", lambda *args: pytest.fail("reused headers scheduled a probe"))
    def download(kwargs, cancel):
        value = response(kwargs["url"])
        observe_response_metadata(kwargs["url"], value, session, stream=False)
    items = [{"url": URL, "mode": "embed", "filename": "result", "_resume_key": "job-v2:1"}]
    result = queue_fixture(items=items, on_download=download, history_recorder=history._record_history)
    entry = json.loads(path.read_text("utf-8"))[URL]
    assert [row["status"] for row in result["results"]] == ["OK"]
    assert entry["metadata_state"] == "ready" and entry["metadata_source"] == "download_response"
    assert entry["etag"] == '"v1"' and entry["content_length"] == "4" and entry["file_name"] == "result"
    assert not threading.current_thread().name.startswith("cyoa-history-")


def test_real_native_headers_and_compressed_fallback_stay_comparable(monkeypatch, tmp_path):
    from cyoa_downloader_app.diagnostics import updates
    from cyoa_downloader_app.network import fetch_base
    from cyoa_downloader_app.network.fetch import fetch_response
    from cyoa_downloader_app.runtime import state, surface  # noqa: F401 -- warm legacy first.
    from cyoa_downloader_app.storage.history_metadata import HistoryMetadataQueue

    calls, revision = [], [1]
    body = b'{"rows":[]}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            probe = self.headers.get("Range") == "bytes=0-0"
            compressed = self.path.startswith("/gzip") and not probe
            calls.append((self.path, probe))
            data = b"x" if probe else gzip.compress(body) if compressed else body
            self.send_response(206 if probe else 200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", f'"{"compressed-" if compressed else ""}v{revision[0]}"')
            if probe: self.send_header("Content-Range", f"bytes 0-0/{len(body)}")
            if compressed: self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    owner = threading.Thread(target=server.serve_forever, daemon=True)
    owner.start()
    queue = HistoryMetadataQueue()
    session = requests.Session()
    session.trust_env = False
    monkeypatch.setattr(state, "_shared_session", session)
    monkeypatch.setattr(state, "_proxy_mode", "disabled")
    monkeypatch.setattr(state, "_domain_min_interval", 0)
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **kwargs: session)
    monkeypatch.setattr(history, "_metadata_queue", queue)
    path = tmp_path / "native-history.json"
    monkeypatch.setattr(history, "_HISTORY_FILE", str(path))
    items = [{"url": f"http://127.0.0.1:{server.server_port}/{case}?q=1", "mode": "embed",
              "filename": case, "_resume_key": f"job-v2:{case}"} for case in ("identity", "gzip")]
    def download(kwargs, cancel):
        result = fetch_response(kwargs["url"], as_bytes=True)
        try:
            assert result.content == body
        finally:
            result.close()
    try:
        result = queue_fixture(items=items, on_download=download, history_recorder=history._record_history)
        assert queue.wait_idle(5)
        assert [row["status"] for row in result["results"]] == ["OK", "OK"]
        assert len(calls) == 3 and sum(probe for _, probe in calls) == 1
        entries = json.loads(path.read_text("utf-8"))
        assert entries[items[0]["url"]]["metadata_source"] == "download_response"
        assert all(row["etag"] == '"v1"' and row["content_length"] == str(len(body)) for row in entries.values())
        assert {row["status"] for row in updates._batch_check_updates(entries)} == {"current"}
        revision[0] = 2
        changes = updates._batch_check_updates(entries)
        assert all(row["status"] == "updated" and "ETag" in row["reason"] for row in changes)
    finally:
        queue.close()
        assert queue.wait_idle(5)
        session.close()
        server.shutdown()
        server.server_close()
        owner.join(2)
