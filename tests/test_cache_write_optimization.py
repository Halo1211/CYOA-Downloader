"""Exercise maintenance through concurrent cache writers, with isolated data."""

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cyoa_downloader_app.storage import cache


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "_CACHE_IDX", tmp_path / "cache" / "index.json")
    for name, value in (
        ("_cache_index", {}), ("_cache_dirty", {}), ("_cache_removed", set()),
        ("_cache_loaded", True), ("_cache_replace_generation", 0),
        ("_cache_flushed_replace_generation", 0),
        ("_cache_write_generation", 0), ("_cache_enforced_generation", -1),
        ("_cache_enforced_scope", None),
    ):
        monkeypatch.setattr(cache, name, value)
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 2048)
    monkeypatch.setattr(cache, "_v465_schedule_cache_save", lambda: None)
    yield cache


def test_overlapping_writes_share_a_completed_budget_scan(isolated_cache, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = cache._enforce_cache_limit
    calls = []

    def enforce():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(cache, "_enforce_cache_limit", enforce)
    payloads = [bytes([number]) * 128 for number in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(cache._cache_put, "image:0", payloads[0])]
        try:
            assert entered.wait(5)
            jobs.extend(pool.submit(cache._cache_put, f"image:{i}", payloads[i]) for i in range(1, 6))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with cache._cache_lock:
                    ready = len(cache._cache_index) == 6
                if ready:
                    break
                time.sleep(0.001)
            assert ready
        finally:
            release.set()
        for job in jobs:
            job.result(timeout=5)
    assert len(calls) == 1, "Every overlapping asset needlessly rescanned the entire disk cache"
    assert [cache._cache_get(f"image:{i}") for i in range(6)] == payloads


def test_each_sequential_write_still_enforces_budget(isolated_cache, monkeypatch):
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 1)
    cache._cache_put("old", b"a" * 700_000)
    cache._cache_put("new", b"b" * 700_000)
    assert cache._cache_get("old") is None
    assert cache._cache_get("new") == b"b" * 700_000
    assert sum(path.stat().st_size for path in cache._CACHE_DIR.glob("*/*")) <= 1024 * 1024


def test_manual_cleanup_includes_external_files(isolated_cache, monkeypatch):
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 1)
    cache._cache_put("local", b"a" * 700_000)
    body = b"b" * 700_000
    digest = hashlib.sha256(body).hexdigest()
    path = cache._CACHE_DIR / digest[:2] / digest
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(body)
    assert cache._enforce_cache_limit() == 1
    assert sum(file.stat().st_size for file in cache._CACHE_DIR.glob("*/*")) <= 1024 * 1024


def test_write_arriving_during_scan_requires_another_pass(isolated_cache, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = cache.os.scandir
    scans = []

    def scan(path):
        if str(path) == str(cache._CACHE_DIR):
            scans.append(1)
            if len(scans) == 1:
                entered.set()
                assert release.wait(5)
        return original(path)

    monkeypatch.setattr(cache.os, "scandir", scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache._cache_put, "first", b"a" * 128)
        try:
            assert entered.wait(5)
            second = pool.submit(cache._cache_put, "second", b"b" * 128)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with cache._cache_lock:
                    ready = len(cache._cache_index) == 2
                if ready:
                    break
                time.sleep(0.001)
            assert ready
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(scans) == 2


@pytest.mark.parametrize("fail_first", [False, True])
def test_waiting_writers_recheck_changed_limit_or_failed_scan(isolated_cache, monkeypatch, fail_first):
    entered = threading.Event()
    release = threading.Event()
    limit = [1 if fail_first else 2]
    original = cache._enforce_cache_limit
    original_scan = cache.os.scandir
    calls = []
    failed = []
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: limit[0])

    def scan(path):
        if fail_first and not failed and str(path) == str(cache._CACHE_DIR):
            failed.append(1)
            raise OSError("temporary scan failure")
        return original_scan(path)

    def enforce():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        result = original()
        # The next waiting writer must observe the new setting even though
        # this scan covered its generation at the former, larger limit.
        limit[0] = 1
        return result

    monkeypatch.setattr(cache, "_enforce_cache_limit", enforce)
    monkeypatch.setattr(cache.os, "scandir", scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache._cache_put, "first", b"a" * 700_000)
        try:
            assert entered.wait(5)
            second = pool.submit(cache._cache_put, "second", b"b" * 700_000)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with cache._cache_lock:
                    ready = len(cache._cache_index) == 2
                if ready:
                    break
                time.sleep(0.001)
            assert ready
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(calls) == 2
    assert sum(file.stat().st_size for file in cache._CACHE_DIR.glob("*/*")) <= 1024 * 1024
