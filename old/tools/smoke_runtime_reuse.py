"""Real browser checks for protected runtime reuse and safe fallback cases."""

from __future__ import annotations

import argparse
import asyncio
import base64
import gzip
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--source-cli", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cyoa-runtime-smoke-") as scratch, patch.dict(os.environ, {"USERPROFILE": scratch}):
        from cyoa_downloader_app.core import cancellation
        from cyoa_downloader_app.core.progress import DownloadCancelledError
        from cyoa_downloader_app.download.website import WebsiteDownloader
        from cyoa_downloader_app.network.runtime_capture import capture_runtime_assets
        from cyoa_downloader_app.runtime import state

        root = Path(scratch)
        bodies = {
            "/payload.svg?variant=one": '<svg xmlns="http://www.w3.org/2000/svg"><text>雪 — one</text></svg>'.encode(),
            "/payload.svg?variant=two": '<svg xmlns="http://www.w3.org/2000/svg"><text>π — two</text></svg>'.encode(),
            "/style.css": b'body{background:url("/payload.svg?variant=one")}',
            "/gzip.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><text>compressed</text></svg>',
            "/unknown.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><text>unknown</text></svg>',
            "/partial.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><text>full resource</text></svg>',
        }
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII=")
        bodies["/large.png"] = png + b"x" * (2 * 1024 * 1024 + 65536)
        counts, browser_counts = {}, {}
        entered = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                browser = bool(self.headers.get("Sec-Fetch-Mode"))
                if urlparse(self.path).path == "/":
                    case = parse_qs(urlparse(self.path).query).get("case", ["reuse"])[0]
                    paths = (list(bodies)[:3] if case != "fallback" else list(bodies)[3:])
                    calls = []
                    for path in paths:
                        encoded = ",".join(str(ord(c)) for c in path)
                        options = ",{headers:{Range:'bytes=0-15'}}" if path == "/partial.svg" else ""
                        calls.append(f"fetch(String.fromCharCode({encoded}){options}).then(r=>r.arrayBuffer())")
                    action = "Promise.all([" + ",".join(calls) + "]).then(()=>{document.title='loaded';this.remove()})"
                    body = f'<html><meta charset="utf-8"><button onclick="{action}">Load More</button></html>'.encode()
                    mime, status = "text/html; charset=utf-8", 200
                elif self.path in bodies:
                    counts[self.path] = counts.get(self.path, 0) + 1
                    if browser:
                        browser_counts[self.path] = browser_counts.get(self.path, 0) + 1
                    if self.path.startswith("/payload") or self.path == "/style.css":
                        if not browser:
                            self.send_response(403)
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        entered.set()
                    body, status = bodies[self.path], 200
                    mime = "text/css" if self.path.endswith(".css") else "image/png" if self.path.endswith(".png") else "image/svg+xml"
                    if self.path == "/gzip.svg":
                        body = gzip.compress(body)
                    if self.path == "/partial.svg" and self.headers.get("Range"):
                        status, body = 206, body[:16]
                else:
                    self.send_error(404)
                    return
                self.send_response(status)
                self.send_header("Content-Type", mime)
                if self.path != "/unknown.svg":
                    self.send_header("Content-Length", str(len(body)))
                if self.path == "/gzip.svg":
                    self.send_header("Content-Encoding", "gzip")
                if status == 206:
                    self.send_header("Content-Range", f"bytes 0-15/{len(bodies[self.path])}")
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        baseline = {child.pid for child in psutil.Process().children(recursive=True)}
        checks = []
        try:
            if args.executable or args.source_cli:
                command = ([str(args.executable.resolve())] if args.executable else
                           [sys.executable, str(Path(__file__).resolve().parents[1] / "cyoa_downloader.py")])
                output = root / "cli"
                log = args.report.with_suffix(".cli.log").resolve()
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("wb") as stream:
                    result = subprocess.run([*command, base + "/?case=reuse", "fixture", "--pure-website-folder",
                        "--archive-strategy", "browser", "--threads", "2",
                        "--proxy-mode", "disabled", "--cloudflare", "off", "--allow-internal-hosts",
                        "--no-ytdlp", "--no-selenium", "--no-deep-scan", "-o", str(output)],
                        cwd=root, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=stream,
                        stderr=subprocess.STDOUT, timeout=120, check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                assert result.returncode == 0, log.read_text("utf-8", errors="replace")[-4000:]
                manifest = json.loads(next(output.rglob("archive_manifest.json")).read_text("utf-8"))
                runtime = manifest["runtime"]
                assert runtime["reused_responses"] == runtime["captured_responses"] == 3, runtime
                for path in list(bodies)[:2]:
                    assert any(file.read_bytes() == bodies[path] for file in output.rglob("*.svg")), path
                assert counts == browser_counts and sum(counts.values()) == 3, counts
                assert not list(output.rglob(".cyoa-runtime-*"))
                checks.append("CLI Browser mode: protected runtime SVG variants/CSS reused once, exact UTF-8 bytes and cleanup")
            else:
                with patch.object(state, "_proxy_mode", "disabled"), patch.object(state, "_domain_min_interval", 0):
                    for case in ("reuse", "fallback"):
                        output = root / case
                        downloader = WebsiteDownloader(base + "/?case=" + case, str(output), archive_strategy="browser")
                        if case == "reuse":
                            failed = base + "/payload.svg?variant=one"
                            downloader._downloaded[failed] = None
                            downloader._failed_items.append({"url": failed, "error": "HTTP 403"})
                        downloader.release_browser_transport()
                        try:
                            async def capture(active_downloader=downloader):
                                return capture_runtime_assets(active_downloader, [active_downloader.start_url], settle_time_ms=350,
                                    capture_interactions=True, max_scroll_steps=1, max_interactions=2)
                            result = asyncio.run(capture())
                            assert not result.failed and result.pages_rendered == 1, result
                            paths = list(bodies)[:3] if case == "reuse" else list(bodies)[3:]
                            for path in paths:
                                local = Path(downloader._downloaded[base + path])
                                if path != "/style.css":
                                    assert local.read_bytes() == bodies[path], path
                            assert result.reused_responses == (3 if case == "reuse" else 0), result
                            if case == "reuse":
                                assert not downloader._failed_items and counts == browser_counts
                                checks.append("Real async-loop capture reuses protected SVG variants/CSS and repairs earlier native failure")
                            else:
                                assert all(counts[path] == 2 for path in paths), counts
                                checks.append("Large, gzip, unknown-size and 206 responses fall back to complete native assets")
                            assert not list(output.glob(".cyoa-runtime-*"))
                        finally:
                            downloader.close()
                    entered.clear()
                    cancel = threading.Event()
                    def cancel_after_response():
                        if entered.wait(15):
                            cancel.set()
                    trigger = threading.Thread(target=cancel_after_response, daemon=True)
                    trigger.start()
                    downloader = WebsiteDownloader(base + "/?case=reuse", str(root / "cancel"), archive_strategy="browser")
                    cancellation.set_progress_event_sink(None, cancel)
                    try:
                        try:
                            capture_runtime_assets(downloader, [downloader.start_url], settle_time_ms=350,
                                capture_interactions=True, max_scroll_steps=1, max_interactions=1)
                            raise AssertionError("Cancellation was swallowed")
                        except DownloadCancelledError:
                            pass
                    finally:
                        cancellation.clear_progress_event_sink()
                        downloader.close()
                        trigger.join(2)
                    assert not list((root / "cancel").glob(".cyoa-runtime-*"))
                    checks.append("Cancellation during real runtime activity closes browser and temporary response store")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        assert not [child.pid for child in psutil.Process().children(recursive=True) if child.pid not in baseline]
        report = {"passed": len(checks), "checks": checks, "asset_requests": counts, "browser_requests": browser_counts}
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
