"""Measure shared HTTP load using two local origins and independent executors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--control-sessions", type=Path, help="Trusted pre-change session source snapshot")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cyoa-network-budget-") as profile, patch.dict(os.environ, {"USERPROFILE": profile}):
        from cyoa_downloader_app.network.budget import HTTP_BUDGET
        from cyoa_downloader_app.network.sessions import create_retry_session
        from cyoa_downloader_app.runtime import state

        if args.control_sessions:
            control = types.ModuleType("cyoa_downloader_app.network._benchmark_control")
            control.__package__ = "cyoa_downloader_app.network"
            exec(compile(args.control_sessions.read_bytes(), str(args.control_sessions), "exec"), vars(control))
            create_retry_session = control.create_retry_session

        state._proxy_mode = "disabled"
        lock = threading.Lock()
        counters = {}
        body = b"fixture-body\x00" * 8192
        digest = hashlib.sha256(body).hexdigest()
        def reset():
            with lock:
                counters.clear()
                counters.update(active=0, peak=0, hosts={}, host_peaks={}, requests=0, throttled=0)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                host = self.headers["Host"].split(":")[0]
                with lock:
                    counters["requests"] += 1
                    active = counters["hosts"].get(host, 0)
                    overloaded = self.path.startswith("/limited") and active >= 12
                    if overloaded:
                        counters["throttled"] += 1
                    else:
                        counters["active"] += 1
                        counters["hosts"][host] = active + 1
                        counters["peak"] = max(counters["peak"], counters["active"])
                        counters["host_peaks"][host] = max(counters["host_peaks"].get(host, 0), active + 1)
                if overloaded:
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                time.sleep(.10)
                # End server-side accounting before the final body becomes
                # readable, so handler teardown cannot inflate observed peaks.
                with lock:
                    counters["active"] -= 1
                    counters["hosts"][host] -= 1
                self.wfile.write(body)

        class Server(ThreadingHTTPServer):
            request_queue_size = 128
        servers = [Server((host, 0), Handler) for host in ("127.0.0.1", "127.0.0.2")]
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
        for thread in threads:
            thread.start()
        samples = []
        try:
            for run in range(args.runs):
                for kind, workers, count in (("healthy", 8, 64), ("mixed", 32, 96), ("limited", 32, 96)):
                    reset()
                    sessions = [create_retry_session() for _ in range(4)]
                    def fetch(index, kind=kind, sessions=sessions):
                        host, port = servers[index % 2].server_address
                        url = f"http://{host}:{port}/{kind}/{index}"
                        with sessions[index % 4].get(url, stream=True, timeout=10) as response:
                            response.raise_for_status()
                            actual = hashlib.sha256()
                            for chunk in response.iter_content(64 * 1024):
                                actual.update(chunk)
                            assert actual.hexdigest() == digest
                    start = time.perf_counter()
                    try:
                        # Four separate executor owners, analogous to asset,
                        # discovery, lookahead and history work sharing transport.
                        with ThreadPoolExecutor(max_workers=workers // 4) as a, ThreadPoolExecutor(max_workers=workers // 4) as b, \
                                ThreadPoolExecutor(max_workers=workers // 4) as c, ThreadPoolExecutor(max_workers=workers // 4) as d:
                            pools = (a, b, c, d)
                            futures = [pools[i % 4].submit(fetch, i) for i in range(count)]
                            for future in futures:
                                future.result()
                    finally:
                        for session in sessions:
                            session.close()
                    samples.append({"run": run, "kind": kind, "seconds": time.perf_counter() - start,
                                    **json.loads(json.dumps(counters))})
                    if not args.control_sessions:
                        snapshot = HTTP_BUDGET.snapshot()
                        assert snapshot["active"] == snapshot["waiting"] == 0
                        assert counters["peak"] <= snapshot["global_limit"]
                        assert max(counters["host_peaks"].values()) <= snapshot["host_limit"]
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(2)
    summary = {kind: {key: statistics.median(row[key] for row in samples if row["kind"] == kind)
                      for key in ("seconds", "requests", "throttled", "peak")}
               for kind in ("healthy", "mixed", "limited")}
    args.output.write_text(json.dumps({"runs": args.runs, "summary": summary, "samples": samples}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
