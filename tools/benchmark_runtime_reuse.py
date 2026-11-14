"""Real Chromium/runtime mirror transfer benchmark on a local file fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sample(args):
    with tempfile.TemporaryDirectory(prefix="cyoa-runtime-bench-") as scratch, patch.dict(os.environ, {"USERPROFILE": scratch}):
        from cyoa_downloader_app.download.website import WebsiteDownloader
        from cyoa_downloader_app.network.runtime_capture import capture_runtime_assets
        from cyoa_downloader_app.runtime import state

        if args.control_runtime:
            module = types.ModuleType("cyoa_downloader_app.network._benchmark_runtime_control")
            module.__package__ = "cyoa_downloader_app.network"
            sys.modules[module.__name__] = module
            exec(compile(args.control_runtime.read_bytes(), str(args.control_runtime), "exec"), module.__dict__)
            capture_runtime_assets = module.capture_runtime_assets
        logging.disable(logging.CRITICAL)
        counts = {}
        release_noise = threading.Event()
        size = args.asset_kib * 1024
        body = b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8)
        expected = hashlib.sha256(body).hexdigest()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                if self.path.startswith("/cdn-cgi/challenge-platform/"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", "4")
                    self.end_headers()
                    release_noise.wait(60)
                    try:
                        self.wfile.write(b"done")
                    except (ConnectionError, OSError):
                        pass
                    return
                if self.path.startswith("/asset/"):
                    counts[self.path] = counts.get(self.path, 0) + 1
                    time.sleep(.025)
                    payload, mime = body, "image/png"
                elif self.path == "/" or self.path.startswith("/p/"):
                    if args.progress_gates:
                        index = int(self.path.rsplit("/", 1)[-1])
                        noise = f"fetch('/cdn-cgi/challenge-platform/hang-{index}');" if args.noise else ""
                        payload = (f"<html><body><button id='gate'>Start</button><script>let index={index},stage=0;"
                            "let seen=Number(sessionStorage.getItem('seen')||0);sessionStorage.setItem('seen',seen+1);"
                            "if(seen!==index){document.getElementById('gate').remove();}else{"
                            "document.getElementById('gate').onclick=function(){"
                            "fetch('/asset/'+(index*3+stage)+'.png').then(r=>r.arrayBuffer());"
                            f"if(stage===0){{{noise}}}"
                            "stage++;if(stage<3)this.textContent=['Start','Continue','Next'][stage];else this.remove();};}"
                            "</script></body></html>").encode()
                    else:
                        payload = ("<html><body><script>Promise.all(Array.from({length:" + str(args.assets) +
                                   "},(_,i)=>fetch('/asset/'+i+'.png').then(r=>r.arrayBuffer()))).then(()=>document.title='ready');"
                                   "</script></body></html>").encode()
                    mime = "text/html"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = Path(scratch) / "output"
        output.mkdir()
        url = f"http://127.0.0.1:{server.server_port}/"
        process = psutil.Process()
        baseline_children = {child.pid for child in process.children(recursive=True)}
        peak, stop = [process.memory_info().rss], threading.Event()
        def monitor():
            while not stop.wait(.01):
                total = process.memory_info().rss
                for child in process.children(recursive=True):
                    try:
                        total += child.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                peak[0] = max(peak[0], total)
        watcher = threading.Thread(target=monitor, daemon=True)
        downloader = WebsiteDownloader(url, str(output), archive_strategy="browser")
        watcher.start()
        started = time.perf_counter()
        try:
            with patch.object(state, "_proxy_mode", "disabled"), patch.object(state, "_domain_min_interval", 0):
                urls = [url + f"p/{index}" for index in range(args.pages)] if args.progress_gates else [url]
                result = capture_runtime_assets(downloader, urls, settle_time_ms=1800 if args.progress_gates else 600,
                    max_scroll_steps=1, capture_interactions=args.progress_gates, max_interactions=3)
            seconds = time.perf_counter() - started
        finally:
            downloader.close()
            stop.set()
            watcher.join(2)
            release_noise.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
        assert len(result.downloaded) == args.assets and not result.failed, result
        if args.progress_gates:
            assert result.pages_rendered == args.pages and result.interactions_attempted == args.assets
        for i in range(args.assets):
            path = Path(downloader._downloaded[url + f"asset/{i}.png"])
            with path.open("rb") as handle:
                assert hashlib.file_digest(handle, "sha256").hexdigest() == expected
        assert not [child.pid for child in process.children(recursive=True) if child.pid not in baseline_children]
        return {"seconds": seconds, "peak_tree_rss_bytes": peak[0], "asset_requests": sum(counts.values()),
                "asset_wire_bytes": sum(counts.values()) * size, "verified_assets": args.assets,
                "captured_responses": getattr(result, "captured_responses", 0),
                "reused_responses": getattr(result, "reused_responses", 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=int, default=12)
    parser.add_argument("--asset-kib", type=int, default=256)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--progress-gates", action="store_true")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--noise", action="store_true")
    parser.add_argument("--control-runtime", type=Path, help="Trusted pre-change runtime source snapshot")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.progress_gates and args.assets != args.pages * 3:
        parser.error("Progress fixture requires three assets/interactions per page")
    if args.noise and not args.progress_gates:
        parser.error("--noise requires --progress-gates")
    if args.sample:
        print(json.dumps(sample(args)))
        return
    samples = []
    for _ in range(args.runs):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--sample", "--assets", str(args.assets),
            "--asset-kib", str(args.asset_kib), "--pages", str(args.pages),
            *(["--progress-gates"] if args.progress_gates else []), *(["--noise"] if args.noise else []),
            *(["--control-runtime", str(args.control_runtime)] if args.control_runtime else [])],
            capture_output=True, text=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        samples.append(json.loads(result.stdout))
    report = {"assets": args.assets, "asset_kib": args.asset_kib, "runs": args.runs, "samples": samples,
              "progress_gates": args.progress_gates, "pages": args.pages, "noise": args.noise,
              "control_runtime": str(args.control_runtime or ""),
              "medians": {key: statistics.median(row[key] for row in samples) for key in samples[0]},
              "scope": "Real Chromium, local HTTP, fresh profile/process; tree RSS; native domain interval disabled"}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
