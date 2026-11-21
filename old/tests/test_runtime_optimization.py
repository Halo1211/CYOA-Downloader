"""Behavior and resource bounds for runtime optimizations (offline fixtures)."""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from urllib3.response import HTTPResponse

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.network import sessions
from cyoa_downloader_app.storage import cache


@pytest.mark.parametrize("option", ["--version", "--help"])
def test_cli_information_does_not_load_gui(option):
    script = Path(__file__).resolve().parents[1] / "cyoa_downloader.py"
    code = (
        "import sys, runpy, json; sys.argv=[sys.argv[1],sys.argv[2]]\n"
        "try: runpy.run_path(sys.argv[0],run_name='__main__')\n"
        "except SystemExit as exc: assert exc.code == 0\n"
        "print(json.dumps({'gui': 'customtkinter' in sys.modules, 'surface': 'cyoa_downloader_app.runtime.surface' in sys.modules}))"
    )
    result = subprocess.run([sys.executable, "-c", code, str(script), option], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"gui": False, "surface": False}


def test_cache_scan_avoids_reopening_every_file_metadata(tmp_path, monkeypatch):
    folder = tmp_path / "00"
    folder.mkdir()
    for index in range(100):
        (folder / f"{index:064x}").write_bytes(b"fixture")
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 2048)
    calls = []
    original = cache._is_link_or_junction

    def checked(path):
        calls.append(str(path))
        return original(path)

    monkeypatch.setattr(cache, "_is_link_or_junction", checked)
    assert cache._enforce_cache_limit() == 0
    # Root and cache bucket guards remain; DirEntry handles per-file metadata.
    assert len(calls) <= 2


@pytest.mark.parametrize("retry_after", [None, "60"])
def test_transport_retry_sleep_is_interruptible(monkeypatch, retry_after):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    monkeypatch.setattr(time, "sleep", lambda seconds: pytest.fail("retry used an uninterruptible sleep"))
    session = sessions.create_retry_session()
    try:
        retry = session.get_adapter("https://").max_retries
        retry = retry.increment(method="GET", url="https://example.test/")
        retry = retry.increment(method="GET", url="https://example.test/")
        response = HTTPResponse(status=503, headers={"Retry-After": retry_after} if retry_after else {})
        with pytest.raises(DownloadCancelledError):
            retry.sleep(response)
    finally:
        session.close()


@pytest.mark.parametrize("retry_after,expected", [(None, 2.0), ("7", 7.0)])
def test_transport_retry_preserves_backoff_and_retry_after(monkeypatch, retry_after, expected):
    sleeps = []
    monkeypatch.setattr(sessions, "_cancel_aware_sleep", sleeps.append)
    with sessions.create_retry_session() as session:
        retry = session.get_adapter("https://").max_retries
        for _ in range(2):
            retry = retry.increment(method="GET", url="https://example.test/")
        retry.sleep(HTTPResponse(status=503, headers={"Retry-After": retry_after} if retry_after else {}))
    assert sleeps == [expected]


@pytest.mark.parametrize("cancel", [False, True])
def test_http_transport_recovers_transient_error_and_can_cancel_retry(monkeypatch, cancel):
    requests = []
    event = threading.Event()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            first = len(requests) == 1
            self.send_response(503 if first else 200)
            self.send_header("Content-Length", "2")
            if first:
                self.send_header("Retry-After", "60" if cancel else "0")
                if cancel:
                    event.set()
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sessions.create_retry_session() as session:
            session.trust_env = False
            session.proxies.clear()
            url = f"http://127.0.0.1:{server.server_port}/asset"
            if cancel:
                started = time.monotonic()
                with pytest.raises(DownloadCancelledError):
                    session.get(url, timeout=2)
                assert time.monotonic() - started < 1.0
                assert requests == ["/asset"]
            else:
                with session.get(url, timeout=2) as response:
                    assert response.status_code == 200 and response.content == b"ok"
                assert requests == ["/asset", "/asset"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_bounded_scheduler_preserves_every_result_without_eager_submission():
    from cyoa_downloader_app.core.concurrency import iter_bounded_results

    consumed = []

    def inputs():
        for value in range(1000):
            consumed.append(value)
            yield value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = iter_bounded_results(pool, lambda value: value * 2, inputs(), max_pending=4)
        first = next(results)
        assert len(consumed) <= 4
        all_results = [first, *results]
    assert sorted(all_results) == [(value, value * 2) for value in range(1000)]


def test_bounded_scheduler_observes_cancel_while_workers_are_blocked():
    from cyoa_downloader_app.core.concurrency import iter_bounded_results

    event = threading.Event()
    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)

    def check():
        if event.is_set():
            raise DownloadCancelledError("fixture cancellation")

    def blocked(value):
        event.set()
        release.wait(3)
        return value

    try:
        started = time.monotonic()
        with pytest.raises(DownloadCancelledError):
            list(iter_bounded_results(pool, blocked, range(1000), max_pending=2, check_cancelled=check))
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("fail", [False, True])
def test_bounded_scheduler_cancels_pending_work_on_early_exit(fail):
    from cyoa_downloader_app.core.concurrency import iter_bounded_results

    submitted = []

    def submit(function, item):
        future = Future()
        if not submitted:
            if fail:
                future.set_exception(OSError("fixture failure"))
            else:
                future.set_result(function(item))
        submitted.append(future)
        return future

    iterator = iter_bounded_results(SimpleNamespace(submit=submit), lambda x: x, range(100), max_pending=4)
    if fail:
        with pytest.raises(OSError, match="fixture failure"):
            next(iterator)
    else:
        assert next(iterator) == (0, 0)
        iterator.close()
    assert len(submitted) == 4
    assert all(future.cancelled() for future in submitted[1:])


def test_image_pipeline_uses_bounded_work_and_preserves_all_embedded_assets(tmp_path, monkeypatch):
    from cyoa_downloader_app.core.media_io import spool_chunks
    from cyoa_downloader_app.download import image_pipeline as pipeline

    payload = b"\x89PNG\r\n\x1a\n" + b"fixture" * 10
    monkeypatch.setattr(pipeline, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(pipeline, "_ssrf_block_cross_origin", lambda *args: False)
    monkeypatch.setattr(pipeline, "_cache_get", lambda url: payload)
    monkeypatch.setattr(pipeline, "_cache_get_file", lambda url, spool: spool_chunks((payload,), spool))
    monkeypatch.setattr(pipeline, "_cache_put_file", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "fetch_response", lambda *args, **kwargs: pytest.fail("cached asset was fetched again"))
    limits = []
    original = pipeline.iter_bounded_results

    def observed(*args, **kwargs):
        limits.append(kwargs["max_pending"])
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "iter_bounded_results", observed)
    project = {"rows": [{"image": f"images/{i}.png"} for i in range(100)]}
    embedded, _downloaded, resolved = pipeline.process_images(
        json.dumps(project), "https://example.test/", embed=True, max_workers=3, output_dir=str(tmp_path),
    )
    assert limits == [6]
    assert all(row["image"].startswith("data:image/png;base64,") for row in json.loads(embedded)["rows"])
    assert len(resolved) == 100


def test_gui_worker_reads_one_consistent_network_settings_snapshot(monkeypatch, tmp_path):
    from cyoa_downloader_app.gui import final_behaviors as gui

    reads = []
    monkeypatch.setattr(gui, "_load_settings", lambda: reads.append(True) or {})
    monkeypatch.setattr(gui, "_set_cloudflare_config", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fixture setup stop")))
    worker = SimpleNamespace(
        _v46_set_event_sink=lambda: None, _on_ytdlp_progress=lambda *a: None,
        _record_speed_bytes=lambda *a: None, _v46_enqueue_progress=lambda *a: None,
        _set_status=lambda *a: None, _run_on_ui_thread=lambda *a: None, _done=lambda: None,
    )
    from cyoa_downloader_app.runtime import state
    monkeypatch.setattr(state, "_gui_speed_cb", None)
    monkeypatch.setattr(state, "_ytdlp_gui_progress_cb", None)
    gui._v46_worker(worker, [], "embed", 0, 1, str(tmp_path), False, False, "off", False, False, 0, False)
    assert reads == [True]
