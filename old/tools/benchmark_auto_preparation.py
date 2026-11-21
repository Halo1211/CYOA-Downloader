"""Measure Auto request count and first-download latency without external HTTP."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.gui import final_behaviors as gui
from cyoa_downloader_app.project import cyoap_vue, discover
from cyoa_downloader_app.runtime import state as runtime_state


def queue_fixture(*, count=12, delay=0.04, first_manual=False, on_download=None, detector=None,
                  items=None, completed=(), history_recorder=None):
    """Exercise the active GUI worker with only network/disk/UI boundaries replaced."""
    items = items if items is not None else [
        {"url": f"https://example.test/{i}/", "mode": "auto", "_run_requested_mode": "auto",
         "_queue_id": str(i), "_run_file_name": str(i), "_resume_key": f"job-v2:{i}"}
        for i in range(count)
    ]
    if first_manual:
        items[0].update(mode="website_folder", _run_requested_mode="website_folder")
    cancel = threading.Event()
    paused = threading.Event()
    paused.set()
    downloads = []
    probes = []
    statuses = []
    worker = SimpleNamespace(
        _cancel_event=cancel, _paused=paused, _active_run_success_ids=set(),
        _run_started_wall=time.time(), _ai_enabled=False, _ai_api_key="",
        _on_ytdlp_progress=lambda *a: None, _record_speed_bytes=lambda *a: None,
        _v46_enqueue_progress=lambda *a: None, _set_status=statuses.append,
        _set_dot=lambda *a: None, _run_on_ui_thread=lambda *a: None,
        _done=lambda: None, _show_results=lambda: None,
    )
    worker._v46_set_event_sink = lambda: cancellation.set_progress_event_sink(None, cancel)
    started = time.perf_counter()

    def detect(url):
        probes.append(url)
        if detector:
            return detector(url, cancel)
        time.sleep(delay)
        return "website_folder"

    def download(**kwargs):
        downloads.append({"seconds": time.perf_counter() - started, "url": kwargs["url"],
                          "file_name": kwargs["file_name"], "website": kwargs["website_output"]})
        if on_download:
            on_download(kwargs, cancel)

    previous_sink = cancellation._PROGRESS_EVENT_SINK
    previous_cancel = cancellation._ACTIVE_CANCEL_EVENT
    try:
        with tempfile.TemporaryDirectory(prefix="cyoa-auto-queue-") as scratch, ExitStack() as stack:
            # The real worker mirrors runtime state for legacy integrations.
            # Keep the harness from leaking those callback/config snapshots.
            modules = [gui, runtime_state, sys.modules.get("cyoa_downloader_app.runtime.surface"),
                       sys.modules.get("cyoa_downloader")]
            for module in modules:
                if module is not None:
                    for name in ("_gui_speed_cb", "_ytdlp_gui_progress_cb", "_ytdlp_enabled",
                                 "_bandwidth_limit_kbps", "wait_time"):
                        stack.enter_context(patch.object(module, name, getattr(module, name, None), create=True))
            for name in ("_set_cloudflare_config", "_set_http2_enabled", "setup_file_logging",
                         "save_resume_state", "clear_resume_state", "write_failed_url_log"):
                stack.enter_context(patch.object(gui, name, lambda *a, **kw: None))
            stack.enter_context(patch.object(gui, "_record_history", history_recorder or (lambda *a, **kw: None)))
            stack.enter_context(patch.object(gui, "_load_settings", lambda: {}))
            stack.enter_context(patch.object(gui, "load_resume_state", lambda *a: {"completed": list(completed), "failed": []}))
            stack.enter_context(patch.object(gui, "auto_detect_mode", detect, create=True))
            if hasattr(gui, "prepare_auto_mode"):
                stack.enter_context(patch.object(gui, "prepare_auto_mode", lambda url: (
                    detected if isinstance(detected := detect(url), discover.AutoDetection)
                    else discover.AutoDetection(detected, url, "fixture"))))
            stack.enter_context(patch.object(discover, "auto_detect_mode", detect))
            stack.enter_context(patch.object(gui, "run_download", download))
            gui._v46_worker(worker, items, "auto", 1, 4, scratch, False, False, "off", False, False, 0, False)
    finally:
        cancellation.set_progress_event_sink(previous_sink, previous_cancel)
    return {"first_download_seconds": downloads[0]["seconds"] if downloads else None,
            "total_seconds": time.perf_counter() - started, "downloads": downloads,
            "probes": probes, "results": worker._last_results, "worker_failed": worker._v46_worker_failed,
            "statuses": statuses}


def missing_site_fixture():
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 404
        response._content = b""
        response.url = url
        return response

    with patch.object(discover, "fetch_response", fetch), patch.object(cyoap_vue, "fetch_response", fetch), \
            patch.object(discover, "_load_settings", lambda: {}):
        mode = discover.auto_detect_mode("https://example.test/game/")
    return {"mode": mode, "requests": len(calls), "unique_requests": len(set(calls)), "network_used": False}


def slow_http_fixture(runs, delay=0.02):
    """Compare against the previous exhaustive algorithm using a local server."""
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(self.path)
            time.sleep(delay)
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        request_queue_size = 32

    server = Server(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_port}/game/"

    def fetch(remote, **kwargs):
        return requests.get(remote, timeout=kwargs.get("timeout", 6), stream=kwargs.get("stream", False))

    def control():
        if cyoap_vue._probe_cyoap_vue_structure(url):
            return "cyoap_vue_folder"
        discover._parallel_head_check(discover.build_default_project_candidates(url))
        return "website_folder"

    samples = {"exhaustive_control": [], "optimized": []}
    try:
        with patch.object(discover, "fetch_response", fetch), patch.object(cyoap_vue, "fetch_response", fetch), \
                patch.object(discover, "_load_settings", lambda: {}):
            for i in range(runs):
                pair = [("exhaustive_control", control), ("optimized", lambda: discover.auto_detect_mode(url))]
                for label, function in pair[::1 if i % 2 == 0 else -1]:
                    before_count = len(requests_seen)
                    started = time.perf_counter()
                    mode = function()
                    samples[label].append({"seconds": time.perf_counter() - started,
                                           "requests": len(requests_seen) - before_count, "mode": mode})
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(2)
    return {"delay_per_response_seconds": delay, "samples": samples, "medians": {
        label: statistics.median(sample["seconds"] for sample in values) for label, values in samples.items()},
        "note": "Local HTTP 404 server; control reconstructs the previous exhaustive candidate algorithm. No internet."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--local-http", action="store_true")
    args = parser.parse_args()
    samples = [queue_fixture() for _ in range(args.runs)]
    result = {"missing_site": missing_site_fixture(), "queue": {"items": 12, "detector_delay_seconds": 0.04,
              "samples": samples, "median_first_download_seconds": statistics.median(
                  sample["first_download_seconds"] for sample in samples)},
              "note": "Mock HTTP 404 and simulated detector delay; no internet timing or real downloads."}
    if args.local_http:
        result["slow_http"] = slow_http_fixture(args.runs)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"missing_site": result["missing_site"], "queue_median_first_download_seconds":
                      result["queue"]["median_first_download_seconds"],
                      "slow_http_medians": result.get("slow_http", {}).get("medians")}, indent=2))


if __name__ == "__main__":
    main()
