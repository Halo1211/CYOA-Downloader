"""Measure history critical-path delay separately from metadata completion."""

from __future__ import annotations

import argparse
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
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cyoa-history-bench-") as scratch, patch.dict(os.environ, {"USERPROFILE": scratch}):
        from cyoa_downloader_app.runtime import state, surface  # noqa: F401 -- warm legacy composition before timing.
        from cyoa_downloader_app.storage import history

        if args.control:
            module = types.ModuleType("cyoa_downloader_app.storage._history_control")
            module.__package__ = "cyoa_downloader_app.storage"
            sys.modules[module.__name__] = module
            exec(compile(args.control.read_bytes(), str(args.control), "exec"), module.__dict__)
            history = module
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                requests.append((self.path, self.headers.get("Range"), self.headers.get("Accept-Encoding")))
                time.sleep(.15)
                self.send_response(206)
                self.send_header("Content-Range", "bytes 0-0/9999")
                self.send_header("Content-Length", "1")
                self.send_header("ETag", '"v1"')
                self.send_header("Last-Modified", "Wed, 11 Nov 2026 00:00:00 GMT")
                self.end_headers()
                try:
                    self.wfile.write(b"x")
                except (ConnectionError, OSError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        samples = []
        try:
            with patch.object(state, "_proxy_mode", "disabled"), patch.object(state, "_domain_min_interval", 0):
                from cyoa_downloader_app.network.sessions import _get_shared_session
                _get_shared_session(False)
                for run in range(args.runs):
                    path = Path(scratch) / f"history-{run}.json"
                    with patch.object(history, "_HISTORY_FILE", str(path)):
                        requests.clear()
                        started = time.perf_counter()
                        for index in range(4):
                            options = {"background": True} if args.background else {}
                            history._record_history(f"http://127.0.0.1:{server.server_port}/{index}", str(index), "embed", True, **options)
                        critical = time.perf_counter() - started
                        immediate = json.loads(path.read_text("utf-8"))
                        assert len(immediate) == 4 and all(row["success"] for row in immediate.values())
                        if args.background:
                            assert history._metadata_queue.wait_idle(10)
                        completed = time.perf_counter() - started
                        saved = json.loads(path.read_text("utf-8"))
                        assert all(row["etag"] == '"v1"' and row["content_length"] == "9999" for row in saved.values())
                        assert len(requests) == 4 and all(value[1:] == ("bytes=0-0", "identity") for value in requests)
                        samples.append({"history_return_seconds": critical, "metadata_complete_seconds": completed,
                                        "probe_requests": len(requests), "success_entries": len(saved)})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        result = {"runs": args.runs, "entries": 4, "server_header_delay_seconds": .15,
                  "background": args.background, "control": str(args.control or ""), "samples": samples,
                  "medians": {key: statistics.median(row[key] for row in samples) for key in samples[0]}}
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
