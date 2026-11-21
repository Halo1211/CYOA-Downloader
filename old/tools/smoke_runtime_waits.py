"""Check delayed runtime assets, real pending gates, and cancellation latency."""

from __future__ import annotations

import argparse
import json
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--source-cli", action="store_true")
    parser.add_argument("--cancel-only", action="store_true")
    parser.add_argument("--control", type=Path, help="Trusted pre-change runtime source snapshot")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cyoa-runtime-waits-") as scratch, patch.dict(os.environ, {"USERPROFILE": scratch}):
        from cyoa_downloader_app.core import cancellation
        from cyoa_downloader_app.core.progress import DownloadCancelledError
        from cyoa_downloader_app.download.website import WebsiteDownloader
        from cyoa_downloader_app.network.runtime_capture import capture_runtime_assets
        from cyoa_downloader_app.runtime import state

        if args.control:
            module = types.ModuleType("cyoa_downloader_app.network._wait_control")
            module.__package__ = "cyoa_downloader_app.network"
            sys.modules[module.__name__] = module
            exec(compile(args.control.read_bytes(), str(args.control), "exec"), module.__dict__)
            capture_runtime_assets = module.capture_runtime_assets
        root = Path(scratch)
        bodies = {path: f'<svg xmlns="http://www.w3.org/2000/svg"><text>雪 {path}</text></svg>'.encode()
                  for path in ("/timer.svg", "/slow.svg", "/next.svg", "/step/0.svg", "/step/1.svg", "/step/2.svg")}
        counts, entered, stamps = {}, {}, {}
        release_noise = threading.Event()

        def url_expression(path):
            return "String.fromCharCode(" + ",".join(str(ord(c)) for c in path) + ")"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path.startswith("/cancel/"):
                    entered[self.path].set()
                    body, mime = b"<html><body>ready</body></html>", "text/html"
                elif self.path == "/timer/":
                    body = ("<html><script>setTimeout(()=>fetch(" + url_expression("/timer.svg") + "),850)</script></html>").encode()
                    mime = "text/html"
                elif self.path == "/slow/":
                    action = ("if(!this.dataset.next){fetch(" + url_expression("/slow.svg") +
                              ");this.dataset.next='1';this.textContent='Continue';}else{fetch(" +
                              url_expression("/next.svg") + ");this.remove()}")
                    body = f'<html><button onclick="{action}">Start</button></html>'.encode()
                    mime = "text/html"
                elif self.path == "/cli/":
                    paths = ",".join(url_expression(f"/step/{i}.svg") for i in range(3))
                    body = ("<html><button id='gate'>Start</button><script>let stage=0;"
                        f"setTimeout(()=>fetch({url_expression('/timer.svg')}),850);"
                        f"let paths=[{paths}];document.getElementById('gate').onclick=function(){{"
                        "fetch(paths[stage]);if(stage===0)fetch(" + url_expression("/cdn-cgi/challenge-platform/hold") +
                        ");stage++;if(stage<3)this.textContent=['Start','Continue','Next'][stage];else this.remove();};"
                        "</script></html>").encode()
                    mime = "text/html"
                elif self.path.startswith("/cdn-cgi/challenge-platform/"):
                    self.send_response(200)
                    self.send_header("Content-Length", "4")
                    self.end_headers()
                    release_noise.wait(60)
                    try:
                        self.wfile.write(b"done")
                    except (ConnectionError, OSError):
                        pass
                    return
                elif self.path in bodies:
                    counts[self.path] = counts.get(self.path, 0) + 1
                    if self.path == "/next.svg":
                        stamps["next"] = time.monotonic()
                    body, mime = bodies[self.path], "image/svg+xml"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.path == "/slow.svg":
                    time.sleep(.85)
                    stamps["slow_finished"] = time.monotonic()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        baseline = {child.pid for child in psutil.Process().children(recursive=True)}
        checks, latencies = [], []
        try:
            if args.executable or args.source_cli:
                command = ([str(args.executable.resolve())] if args.executable else
                           [sys.executable, str(Path(__file__).resolve().parents[1] / "cyoa_downloader.py")])
                log = args.report.with_suffix(".cli.log").resolve()
                log.parent.mkdir(parents=True, exist_ok=True)
                output = root / "output"
                with log.open("wb") as stream:
                    result = subprocess.run([*command, base + "/cli/", "fixture", "--pure-website-folder",
                        "--archive-strategy", "browser", "--proxy-mode", "disabled", "--cloudflare", "off",
                        "--allow-internal-hosts", "--no-ytdlp", "--no-selenium", "--no-deep-scan", "-o", str(output)],
                        cwd=root, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=stream, stderr=subprocess.STDOUT,
                        timeout=120, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                assert result.returncode == 0, log.read_text("utf-8", errors="replace")[-4000:]
                runtime = json.loads(next(output.rglob("archive_manifest.json")).read_text("utf-8"))["runtime"]
                assert runtime["reused_responses"] == 4 and runtime["interactions_attempted"] == 3, runtime
                for path in ("/timer.svg", "/step/0.svg", "/step/1.svg", "/step/2.svg"):
                    assert counts[path] == 1 and any(file.read_bytes() == bodies[path] for file in output.rglob("*.svg")), path
                assert not list(output.rglob(".cyoa-runtime-*"))
                checks.append("CLI: delayed timer plus three gates with pending noise; four exact assets reused and cleaned")
            else:
                with patch.object(state, "_proxy_mode", "disabled"), patch.object(state, "_domain_min_interval", 0):
                    if not args.cancel_only:
                        for case, paths in (("timer", ("/timer.svg",)), ("slow", ("/slow.svg", "/next.svg"))):
                            downloader = WebsiteDownloader(base + "/" + case + "/", str(root / case), archive_strategy="browser")
                            try:
                                result = capture_runtime_assets(downloader, [downloader.start_url],
                                    settle_time_ms=1100 if case == "timer" else 1800, capture_interactions=case == "slow",
                                    max_scroll_steps=1, max_interactions=2)
                                assert not result.failed and result.reused_responses == len(paths), result
                                for path in paths:
                                    assert counts[path] == 1 and Path(downloader._downloaded[base + path]).read_bytes() == bodies[path]
                                assert not list((root / case).glob(".cyoa-runtime-*"))
                            finally:
                                downloader.close()
                            if case == "slow":
                                assert stamps["next"] >= stamps["slow_finished"], stamps
                            checks.append(case + ": complete asset bytes retained with the original settling window")
                    for run in range(args.runs):
                        path, cancel, cancelled_at = f"/cancel/{run}", threading.Event(), []
                        entered[path] = threading.Event()
                        def trigger(event=entered[path], current_cancel=cancel, clock=cancelled_at):
                            if event.wait(15):
                                time.sleep(.2)
                                clock.append(time.monotonic())
                                current_cancel.set()
                        actor = threading.Thread(target=trigger, daemon=True)
                        downloader = WebsiteDownloader(base + path, str(root / f"cancel-{run}"), archive_strategy="browser")
                        cancellation.set_progress_event_sink(None, cancel)
                        actor.start()
                        try:
                            try:
                                capture_runtime_assets(downloader, [downloader.start_url], settle_time_ms=1800, max_scroll_steps=1)
                                raise AssertionError("Cancellation was swallowed")
                            except DownloadCancelledError:
                                assert cancelled_at
                                latency = time.monotonic() - cancelled_at[0]
                                latencies.append(latency)
                                if not args.control:
                                    assert latency < 1.2, latency
                        finally:
                            cancellation.clear_progress_event_sink()
                            downloader.close()
                            actor.join(2)
                        assert not list((root / f"cancel-{run}").glob(".cyoa-runtime-*"))
                        checks.append("cancel: browser and temporary store closed")
        finally:
            release_noise.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
        assert not [child.pid for child in psutil.Process().children(recursive=True) if child.pid not in baseline]
        report = {"passed": len(checks), "checks": checks, "asset_requests": counts,
                  "cancel_seconds": latencies, "median_cancel_seconds": statistics.median(latencies) if latencies else None,
                  "control": str(args.control or ""), "executable": str(args.executable or "source")}
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
