"""Count real media HTTP attempts; record planned waits without sleeping."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import sys
import tempfile
import threading
import types
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not args.verbose:
        logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="cyoa-status-retries-") as scratch, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USERPROFILE": scratch}))
        from cyoa_downloader_app.download import image_pipeline
        from cyoa_downloader_app.network import fetch_base, sessions, throttle
        from cyoa_downloader_app.network.budget import HTTP_BUDGET
        from cyoa_downloader_app.runtime import state, surface
        from cyoa_downloader_app.storage import cache

        pipeline = image_pipeline
        if args.control:
            pipeline = types.ModuleType("cyoa_downloader_app.download._status_control")
            pipeline.__package__ = "cyoa_downloader_app.download"
            exec(compile(args.control.read_bytes(), str(args.control), "exec"), pipeline.__dict__)
        counts, records, waits = {}, [], []
        body = b"\x89PNG\r\n\x1a\n" + b"fixture" * 1000

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                counts[self.path] = counts.get(self.path, 0) + 1
                status = int(self.path.split("/")[1])
                recovered = "recover" in self.path and counts[self.path] >= 4
                content = body if recovered else b"unavailable"
                self.send_response(200 if recovered else status)
                self.send_header("Content-Type", "image/png" if recovered else "text/plain")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Retry-After", "2")
                self.end_headers()
                self.wfile.write(content)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join, 2)
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        for target, name, value in (
            (pipeline, "_cache_get_file", lambda *a: None),
            (pipeline, "_cache_put_file", lambda *a: None),
            (pipeline, "_make_cookie_session", lambda *a: None),
            (pipeline, "_SELENIUM_ENABLED", False),
            (pipeline, "_domain_record_failure", lambda *a: 0),
            (pipeline, "_domain_record_success", lambda *a: None),
            (pipeline, "_throttle_bandwidth", lambda *a: None),
            (pipeline, "_cancel_aware_sleep", lambda delay: waits.append(("asset", delay))),
            (sessions, "_cancel_aware_sleep", lambda delay: waits.append(("transport", delay))),
            (throttle, "_cancel_aware_sleep", lambda delay: None),
            (fetch_base, "_domain_throttle", lambda *a: None),
            (state, "_proxy_mode", "disabled"),
            (state, "_CLOUDFLARE_MODE", "off"),
            (surface, "_CLOUDFLARE_MODE", "off"),
            (cache, "_v465_schedule_cache_save", lambda: None),
        ):
            stack.enter_context(patch.object(target, name, value))
        sessions._v465_reset_shared_sessions()
        stack.callback(sessions._v465_reset_shared_sessions)
        for status in (429, 503):
            for recover in (False, True):
                for run in range(args.runs):
                    path = f"/{status}/{'recover' if recover else 'fail'}-{run}.png"
                    url = f"http://127.0.0.1:{server.server_port}{path}"
                    output = Path(scratch) / f"out-{status}-{recover}-{run}"
                    waits.clear()
                    _, localized, resolved = pipeline.process_images(
                        json.dumps({"image": url}), url, download=True,
                        temp_folder=str(output), max_workers=1, wait_time=2,
                    )
                    target = json.loads(localized)["image"]
                    if recover:
                        saved = output / target
                        assert saved.is_file() and hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(body).digest()
                        assert resolved
                    else:
                        assert target == url and not resolved
                    assert not list(output.rglob("*.part"))
                    assert HTTP_BUDGET.snapshot()["active"] == 0
                    records.append({"status": status, "recover": recover, "run": run,
                                    "http_requests": counts[path], "waits": list(waits),
                                    "planned_wait_seconds": sum(delay for _, delay in waits),
                                    "bytes_verified": len(body) if recover else 0})
        summary = []
        for status in (429, 503):
            for recover in (False, True):
                group = [row for row in records if row["status"] == status and row["recover"] == recover]
                summary.append({"status": status, "recover": recover,
                                "http_requests": statistics.median(row["http_requests"] for row in group),
                                "planned_wait_seconds": statistics.median(row["planned_wait_seconds"] for row in group)})
        result = {"control": str(args.control or ""), "runs": args.runs, "samples": records,
                  "medians": summary, "waits_virtualized": True, "domain_throttle_excluded": True}
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
