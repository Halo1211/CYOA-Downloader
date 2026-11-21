"""Deferred history metadata never changes completed-job identity or status."""

import json
import threading
from types import SimpleNamespace

import pytest
import requests

from cyoa_downloader_app.diagnostics import updates
from cyoa_downloader_app.network import proxy, sessions
from cyoa_downloader_app.runtime import state
from cyoa_downloader_app.storage import history
from cyoa_downloader_app.storage.history_metadata import HistoryMetadataQueue
from tools.benchmark_auto_preparation import queue_fixture


@pytest.fixture
def environment(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    queue = HistoryMetadataQueue(max_pending=2)
    signature = ["same"]
    session = object()
    monkeypatch.setattr(history, "_HISTORY_FILE", str(path))
    monkeypatch.setattr(history, "_metadata_queue", queue)
    monkeypatch.setattr(history, "_metadata_context", lambda url: (session, signature[0]))
    monkeypatch.setattr(history, "_probe_history_metadata", lambda url: ({"etag": '"v1"', "content_length": "9"}, "ready"))
    yield path, queue, signature
    queue.close()
    assert queue.wait_idle(5)


def saved(path):
    return json.loads(path.read_text("utf-8"))


def blocking_probe(monkeypatch):
    entered, release, calls = threading.Event(), threading.Event(), []
    def probe(url):
        calls.append(url)
        entered.set()
        assert release.wait(3)
        return {"etag": f'"v{len(calls)}"', "content_length": "9"}, "ready"
    monkeypatch.setattr(history, "_probe_history_metadata", probe)
    return entered, release, calls


def test_optional_context_failure_preserves_success(environment, monkeypatch):
    path, queue, _ = environment
    def failed_context(url):
        raise KeyError("optional context unavailable")
    monkeypatch.setattr(history, "_metadata_context", failed_context)
    history._record_history("https://example.test/a", "complete", "zip", True, background=True)
    entry = saved(path)["https://example.test/a"]
    assert entry["success"] and entry["file_name"] == "complete"
    assert entry["metadata_state"] == "unavailable" and queue.wait_idle(1)


def test_success_is_persisted_before_slow_probe_returns(environment, monkeypatch):
    path, queue, _ = environment
    entered, release, _ = blocking_probe(monkeypatch)
    try:
        history._record_history("https://example.test/a", "雪", "zip", True, background=True)
        assert entered.wait(1)
        entry = saved(path)["https://example.test/a"]
        assert entry["success"] and entry["file_name"] == "雪" and entry["metadata_state"] == "pending"
        release.set()
        assert queue.wait_idle(2)
        complete = saved(path)["https://example.test/a"]
        assert complete["etag"] == '"v1"' and complete["metadata_state"] == "ready"
        assert complete["last_downloaded"] == entry["last_downloaded"] and complete["mode"] == "zip"
    finally:
        release.set()


@pytest.mark.parametrize("replacement", ["failure", "delete", "new_success"])
def test_old_probe_cannot_overwrite_newer_record_or_resurrect_deleted_url(environment, monkeypatch, replacement):
    path, queue, _ = environment
    entered, release, _ = blocking_probe(monkeypatch)
    url = "https://example.test/a"
    try:
        history._record_history(url, "old", "zip", True, background=True)
        assert entered.wait(1)
        if replacement == "failure":
            history._record_history(url, "new", "embed", False)
        elif replacement == "delete":
            history._save_history({})
        else:
            history._record_history(url, "new", "embed", True, background=True)
        release.set()
        assert queue.wait_idle(2)
        current = saved(path)
        if replacement == "delete":
            assert url not in current
        else:
            assert current[url]["file_name"] == "new" and current[url]["mode"] == "embed"
            assert current[url]["success"] == (replacement == "new_success")
            if replacement == "failure":
                assert "etag" not in current[url]
            else:
                assert current[url]["etag"] == '"v2"'
    finally:
        release.set()


def test_background_writer_keeps_original_history_path(environment, monkeypatch, tmp_path):
    path, queue, _ = environment
    entered, release, _ = blocking_probe(monkeypatch)
    other = tmp_path / "other.json"
    other.write_text('{"sentinel":{}}')
    try:
        history._record_history("https://example.test/a", "a", "zip", True, background=True)
        assert entered.wait(1)
        monkeypatch.setattr(history, "_HISTORY_FILE", str(other))
        release.set()
        assert queue.wait_idle(2)
        assert saved(path)["https://example.test/a"]["etag"] == '"v1"'
        assert saved(other) == {"sentinel": {}}
    finally:
        release.set()


def test_changed_network_or_cookie_context_discards_probe_metadata(environment, monkeypatch):
    path, queue, signature = environment
    entered, release, _ = blocking_probe(monkeypatch)
    try:
        history._record_history("https://example.test/a", "a", "zip", True, background=True)
        assert entered.wait(1)
        signature[0] = "new cookies/profile"
        release.set()
        assert queue.wait_idle(2)
        entry = saved(path)["https://example.test/a"]
        assert entry["success"] and entry["metadata_state"] == "unavailable" and "etag" not in entry
    finally:
        release.set()


def test_cancelled_owner_stays_cancelled_after_gui_event_is_cleared(environment, monkeypatch):
    path, queue, _ = environment
    entered, release, calls = blocking_probe(monkeypatch)
    owner = threading.Event()
    try:
        for index in range(2):
            history._record_history(f"https://example.test/{index}", str(index), "zip", True,
                                    background=True, cancel_event=owner)
            if index == 0:
                assert entered.wait(1)
        owner.set()
        history._cancel_history_metadata(owner)
        owner.clear()
        release.set()
        assert queue.wait_idle(2)
        assert len(calls) == 1
        assert all(row["success"] and row["metadata_state"] == "cancelled" for row in saved(path).values())
    finally:
        release.set()


def test_pending_queue_is_bounded_and_repeated_url_is_coalesced(environment, monkeypatch):
    path, queue, _ = environment
    entered, release, calls = blocking_probe(monkeypatch)
    try:
        history._record_history("https://example.test/0", "0", "zip", True, background=True)
        assert entered.wait(1)
        for index in range(1, 4):
            history._record_history(f"https://example.test/{index}", str(index), "zip", True, background=True)
        history._record_history("https://example.test/1", "latest", "both", True, background=True)
        assert queue.snapshot() == {"pending": 2, "active": 1, "limit": 2}
        assert saved(path)["https://example.test/3"]["metadata_state"] == "skipped"
        release.set()
        assert queue.wait_idle(2) and len(calls) == 3
        assert "https://example.test/3" not in calls
        assert saved(path)["https://example.test/1"]["file_name"] == "latest"
    finally:
        release.set()


def test_optional_backend_exception_does_not_strand_pending_history(environment, monkeypatch):
    path, queue, _ = environment
    def broken(url):
        raise KeyError("optional fixture backend")
    monkeypatch.setattr(history, "_probe_history_metadata", broken)
    history._record_history("https://example.test/a", "a", "zip", True, background=True)
    assert queue.wait_idle(2)
    entry = saved(path)["https://example.test/a"]
    assert entry["success"] and entry["metadata_state"] == "unavailable"


@pytest.mark.parametrize("metadata_state", ["pending", "unavailable", "cancelled", "skipped"])
def test_incomplete_metadata_is_never_reported_as_current(monkeypatch, metadata_state):
    monkeypatch.setattr(updates, "fetch_response", lambda *a, **kw: pytest.fail("No baseline to compare"))
    results = updates._batch_check_updates({"https://example.test/a": {
        "success": True, "metadata_state": metadata_state, "filename": "雪",
    }})
    assert results[0]["status"] == "error" and results[0]["reason"] and results[0]["name"] == "雪"


def test_ready_zero_length_metadata_stays_comparable(monkeypatch):
    response = SimpleNamespace(status_code=200, headers={"Content-Length": "0"}, close=lambda: None)
    monkeypatch.setattr(updates, "fetch_response", lambda *a, **kw: response)
    result = updates._batch_check_updates({"https://example.test/a": {
        "success": True, "metadata_state": "ready", "content_length": "0",
    }})
    assert result[0]["status"] == "current"


def test_context_fingerprint_separates_session_cookies_headers_and_proxy(monkeypatch):
    session = requests.Session()
    monkeypatch.setattr(state, "_shared_session", session)
    monkeypatch.setattr(sessions, "_get_shared_session", lambda *a: session)
    monkeypatch.setattr(proxy, "_get_active_proxies", lambda: {})
    original = history._metadata_context("https://example.test/a")
    session.cookies.set("other", "one", domain="other.test")
    assert history._metadata_context("https://example.test/a") == original
    session.cookies.set("auth", "one", domain="example.test")
    first = history._metadata_context("https://example.test/a")
    assert first != original
    session.headers["Authorization"] = "fixture-only"
    second = history._metadata_context("https://example.test/a")
    assert second != first
    monkeypatch.setattr(state, "_proxy_mode", "manual")
    assert history._metadata_context("https://example.test/a") != second


def test_active_gui_worker_starts_next_item_while_metadata_is_blocked(environment, monkeypatch):
    _, queue, _ = environment
    entered, release, _ = blocking_probe(monkeypatch)
    seen = []
    def downloaded(kwargs, cancel):
        seen.append(kwargs["url"])
        if len(seen) == 2:
            assert entered.wait(1) and not release.is_set()
    try:
        result = queue_fixture(count=2, delay=0, on_download=downloaded, history_recorder=history._record_history)
        assert len(seen) == 2 and not result["worker_failed"]
        assert [row["status"] for row in result["results"]] == ["OK", "OK"]
    finally:
        release.set()
    assert queue.wait_idle(2)


def test_gui_cancel_retires_previous_success_probes_without_relabelling_rows(environment, monkeypatch):
    path, queue, _ = environment
    entered, release, _ = blocking_probe(monkeypatch)
    seen = []
    def downloaded(kwargs, cancel):
        seen.append(kwargs["url"])
        if len(seen) == 2:
            assert entered.wait(1)
            cancel.set()
    try:
        result = queue_fixture(count=2, delay=0, on_download=downloaded, history_recorder=history._record_history)
        assert [row["status"] for row in result["results"]] == ["OK", "CANCELLED"]
    finally:
        release.set()
    assert queue.wait_idle(2)
    assert len(saved(path)) == 1
    assert next(iter(saved(path).values()))["metadata_state"] == "cancelled"
