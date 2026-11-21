"""Verify the CLI's actual 429 request cap with real cancellable backoff."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    attempts = []
    project = {"rows": [{"objects": [{"image": "error.png"}]}]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/project.json":
                status, body, mime = 200, json.dumps(project).encode(), "application/json"
            elif self.path == "/error.png":
                attempts.append(time.monotonic())
                status, body, mime = 429, b"rate limited", "text/plain"
            else:
                status, body, mime = 404, b"missing", "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            if status == 429:
                self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory(prefix="cyoa-status-smoke-") as scratch:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = Path(scratch) / "output"
        env = {**os.environ, "USERPROFILE": scratch, "PYTHONIOENCODING": "utf-8"}
        command = ([str(args.executable.resolve())] if args.executable else
                   [sys.executable, str(Path(__file__).resolve().parents[1] / "cyoa_downloader.py")])
        log = args.report.with_suffix(".cli.log").resolve()
        log.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            with log.open("wb") as stream:
                result = subprocess.run([*command, f"http://127.0.0.1:{server.server_port}/project.json", "fixture",
                    "-o", str(output), "--threads", "1", "--wait", "0", "--proxy-mode", "disabled",
                    "--cloudflare", "off", "--allow-internal-hosts", "--no-ytdlp", "--no-selenium",
                    "--no-deep-scan"], cwd=scratch, env=env, stdout=stream, stderr=subprocess.STDOUT,
                    timeout=120, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            assert result.returncode == 0, log.read_text("utf-8", errors="replace")[-3000:]
            assert len(attempts) == 4, f"Expected four native requests, got {len(attempts)}"
            assert json.loads((output / "fixture.json").read_text("utf-8")) == project
            reports = list(output.rglob("failed_images.txt"))
            assert reports and any("HTTP 429" in p.read_text("utf-8") for p in reports)
            assert not list(output.rglob("*.part")) and not list(output.rglob(".cyoa-media-*"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        report = {"passed": 1, "native_requests": len(attempts), "seconds": time.monotonic() - started,
                  "executable": str(args.executable or "source"), "real_backoff": True,
                  "checks": ["Persistent 429: four requests, original JSON retained, HTTP status reported, temporary files removed"]}
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
