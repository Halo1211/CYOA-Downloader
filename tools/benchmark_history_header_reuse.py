"""Measure extra history probes on the active GUI worker using local HTTP."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cyoa-header-bench-") as scratch, patch.dict(os.environ, {"USERPROFILE": scratch}):
        from cyoa_downloader_app.network.fetch import fetch_response
        from cyoa_downloader_app.network.sessions import _get_shared_session
        from cyoa_downloader_app.runtime import state, surface  # noqa: F401 -- warm legacy composition.
        from cyoa_downloader_app.storage import history
        from tools.benchmark_auto_preparation import queue_fixture

        if args.control:
            module = types.ModuleType("cyoa_downloader_app.storage._header_control")
            module.__package__ = "cyoa_downloader_app.storage"
            sys.modules[module.__name__] = module
            exec(compile(args.control.read_bytes(), str(args.control), "exec"), module.__dict__)
            history = module
        calls = []
        body = b'{"title":"fixture","rows":[]}'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                is_probe = self.headers.get("Range") is not None
                calls.append({"path": self.path, "probe": is_probe, "encoding": self.headers.get("Accept-Encoding")})
                time.sleep(.10)
                encoded = self.path.startswith("/gzip/") and not is_probe
                payload = b"x" if is_probe else gzip.compress(body) if encoded else body
                self.send_response(206 if is_probe else 200)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("ETag", '"gzip-v1"' if encoded else '"v1"')
                if is_probe: self.send_header("Content-Range", f"bytes 0-0/{len(body)}")
                if encoded: self.send_header("Content-Encoding", "gzip")
                if self.path.startswith("/vary/"): self.send_header("Vary", "User-Agent")
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        owner = threading.Thread(target=server.serve_forever, daemon=True)
        owner.start()
        samples = {case: [] for case in ("identity", "gzip", "vary")}
        try:
            with patch.object(state, "_proxy_mode", "disabled"), patch.object(state, "_domain_min_interval", 0):
                _get_shared_session(False)
                def download(kwargs, cancel):
                    result = fetch_response(kwargs["url"], as_bytes=True)
                    assert result is not None
                    try:
                        assert result.content == body
                    finally:
                        result.close()
                for case, rows in samples.items():
                    for run in range(args.runs):
                        path = Path(scratch) / f"{case}-{run}.json"
                        items = [{"url": f"http://127.0.0.1:{server.server_port}/{case}/{index}?q=1",
                                  "mode": "embed", "filename": str(index), "_resume_key": f"job-v2:{index}"}
                                 for index in range(4)]
                        with patch.object(history, "_HISTORY_FILE", str(path)):
                            calls.clear()
                            start = time.perf_counter()
                            result = queue_fixture(items=items, on_download=download, history_recorder=history._record_history)
                            returned = time.perf_counter() - start
                            assert history._metadata_queue.wait_idle(10)
                            completed = time.perf_counter() - start
                            entries = json.loads(path.read_text("utf-8"))
                            assert all(row["status"] == "OK" for row in result["results"])
                            assert len(entries) == 4 and all(row["success"] and row["metadata_state"] == "ready"
                                and row["etag"] == '"v1"' and row["content_length"] == str(len(body)) for row in entries.values())
                            probes = [call for call in calls if call["probe"]]
                            expected = 0 if not args.control and case == "identity" else 4
                            assert len(probes) == expected and len(calls) == 4 + expected, calls
                            assert all(call["encoding"] == "identity" for call in probes)
                            rows.append({"queue_return_seconds": returned, "metadata_complete_seconds": completed,
                                         "total_requests": len(calls), "extra_history_probes": len(probes),
                                         "success_entries": len(entries)})
        finally:
            server.shutdown()
            server.server_close()
            owner.join(2)
        report = {"control": str(args.control or ""), "runs": args.runs, "entries": 4,
                  "server_header_delay_seconds": .10, "samples": samples,
                  "medians": {case: {key: statistics.median(row[key] for row in rows) for key in rows[0]}
                              for case, rows in samples.items()},
                  "scope": "Real HTTP through active GUI worker; actual archive/UI boundaries replaced by harness"}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
