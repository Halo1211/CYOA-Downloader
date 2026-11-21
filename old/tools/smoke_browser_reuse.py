"""Local Chromium lifecycle, isolation and browser-only website smoke checks."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--source-cli", action="store_true")
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix="cyoa-browser-smoke-") as scratch, ExitStack() as patches:
        patches.enter_context(patch.dict(os.environ, {"USERPROFILE": scratch}))
        import requests

        from cyoa_downloader_app.core import cancellation
        from cyoa_downloader_app.core.media_io import FilePayload
        from cyoa_downloader_app.core.progress import DownloadCancelledError
        from cyoa_downloader_app.download import website
        from cyoa_downloader_app.network import browser

        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII=")
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        js = 'window.fixture = "雪 — α";'.encode()
        assets = {"/": (b'<html><head><link rel="stylesheet" href="style.css"></head><body><img src="img.svg"><script src="app.js"></script></body></html>', "text/html"),
                  "/style.css": (b"body{background:url(img.svg)}", "text/css"),
                  "/img.svg": (svg, "image/svg+xml"), "/app.js": (js, "application/javascript")}
        entered, release = threading.Event(), threading.Event()
        counters = {"rejected_http": 0, "browser_requests": 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                if (args.executable or args.source_cli) and self.headers.get("Sec-Fetch-Mode") != "navigate":
                    counters["rejected_http"] += 1
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                counters["browser_requests"] += 1
                if self.path == "/echo":
                    body = f"<html>{self.headers.get('Cookie', '')}|{self.headers.get('User-Agent', '')}</html>".encode()
                    mime = "text/html"
                else:
                    body, mime = assets.get(self.path, (png, "image/png"))
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                if self.path == "/set":
                    self.send_header("Set-Cookie", "browser_only=one; Path=/")
                self.end_headers()
                if self.path == "/slow":
                    entered.set()
                    release.wait(3)
                try:
                    self.wfile.write(body)
                except (ConnectionError, OSError):
                    pass
        servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
        for thread in threads:
            thread.start()
        urls = [f"http://127.0.0.1:{s.server_port}" for s in servers]
        baseline = {p.pid for p in psutil.Process().children(recursive=True)}
        try:
            if args.executable or args.source_cli:
                output = Path(scratch) / "output"
                log = args.report.with_suffix(".cli.log").resolve()
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("wb") as handle:
                    command = ([str(args.executable.resolve())] if args.executable else
                               [sys.executable, str(Path(__file__).resolve().parents[1] / "cyoa_downloader.py")])
                    process = subprocess.Popen([*command, urls[0] + "/", "fixture",
                        "--pure-website-folder", "--archive-strategy", "auto", "--threads", "4",
                        "--proxy-mode", "disabled", "--cloudflare", "off", "--allow-internal-hosts",
                        "--no-ytdlp", "--no-selenium", "--no-deep-scan", "-o", str(output)],
                        cwd=scratch, env=dict(os.environ), stdout=handle, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    tracked, owner, deadline = {}, psutil.Process(process.pid), time.monotonic() + 90
                    while process.poll() is None:
                        try:
                            children = owner.children(recursive=True)
                        except psutil.NoSuchProcess:
                            children = []
                        for child in children:
                            try:
                                tracked[child.pid] = child.create_time()
                            except psutil.NoSuchProcess:
                                pass
                        if time.monotonic() > deadline:
                            for pid, created in reversed(list(tracked.items())):
                                try:
                                    child = psutil.Process(pid)
                                    if child.create_time() == created:
                                        child.kill()
                                except psutil.NoSuchProcess:
                                    pass
                            process.kill()
                            process.wait()
                            raise RuntimeError(f"Browser CLI smoke timed out; see {log}")
                        time.sleep(.05)
                assert process.returncode == 0, log.read_text("utf-8", errors="replace")[-4000:]
                assert any(p.name == "index.html" for p in output.rglob("*"))
                for suffix, body in ((".svg", svg), (".js", js)):
                    assert any(p.read_bytes() == body for p in output.rglob("*" + suffix))
                assert counters["rejected_http"] and counters["browser_requests"] >= 4
                for pid, created in tracked.items():
                    try:
                        child = psutil.Process(pid)
                        if child.create_time() == created:
                            child.wait(3)
                    except psutil.NoSuchProcess:
                        pass
                checks.append("Browser-only CLI website: HTML/CSS/JS/SVG, UTF-8, fallback and child-process cleanup")
            else:
                patches.enter_context(patch.object(browser, "vpn_requirement_satisfied", lambda: True))
                patches.enter_context(patch.object(browser, "_get_browser_proxy_config", lambda url: {}))
                http = requests.Session()
                http.headers["User-Agent"] = "Owner fixture"
                patches.enter_context(patch.object(browser, "_get_shared_session", lambda *a: http))
                session = browser.BrowserFetchSession()
                try:
                    session.fetch(urls[0] + "/set")
                    assert b"browser_only=one" in session.fetch(urls[0] + "/echo").content
                    assert b"browser_only" not in session.fetch(urls[1] + "/echo").content
                    http.cookies.set("auth", "seeded", domain="127.0.0.1", path="/")
                    assert b"auth=seeded" in session.fetch(urls[1] + "/echo").content
                    checks.append("Real contexts isolate origins and seed matching HTTP cookies/user-agent")
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        results = list(pool.map(lambda i: session.fetch(urls[1] + f"/img{i}", file_backed=True), range(8)))
                    for result in results:
                        assert isinstance(result.content, FilePayload)
                        assert Path(result.content.path).read_bytes() == png
                    assert session.stats["launches"] == 1
                    spool = Path(results[0].content.path).parent
                finally:
                    session.close()
                assert not spool.exists()
                checks.append("Eight parallel callers use one real browser owner and file-backed results")
                with patch.object(website, "fetch_response", lambda *a, **kw: None):
                    downloader = website.WebsiteDownloader(urls[0] + "/", scratch, archive_strategy="auto")
                    def read(i):
                        response = downloader._fetch(urls[0] + f"/img{i}")
                        try:
                            assert response._content_consumed is False
                            return b"".join(response.iter_content(11))
                        finally:
                            response.close()
                    try:
                        with ThreadPoolExecutor(max_workers=4) as pool:
                            assert list(pool.map(read, range(8))) == [png] * 8
                        assert downloader._browser_fetch_session.stats["launches"] == 1
                        assert not list(Path(downloader._browser_fetch_session._spool.name).iterdir())
                    finally:
                        downloader.close()
                checks.append("Actual WebsiteDownloader parallel fallback streams, closes and deletes response files")
                cancel = threading.Event()
                session = browser.BrowserFetchSession()
                session.fetch(urls[0] + "/img")
                cancellation.set_progress_event_sink(None, cancel)
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pending = pool.submit(session.fetch, urls[0] + "/slow", timeout_ms=2000)
                        assert entered.wait(5)
                        start = time.monotonic()
                        cancel.set()
                        try:
                            pending.result(1)
                            raise AssertionError("Cancellation was swallowed")
                        except DownloadCancelledError:
                            assert time.monotonic() - start < .3
                        release.set()
                    session.close()
                finally:
                    release.set()
                    session.close()
                    cancellation.clear_progress_event_sink()
                checks.append("Cancellation returns promptly and owner shutdown joins active work")
        finally:
            release.set()
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(p.pid not in baseline for p in psutil.Process().children(recursive=True)):
            time.sleep(.05)
        assert not [p.pid for p in psutil.Process().children(recursive=True) if p.pid not in baseline]
    args.report.write_text(json.dumps({"passed": len(checks), "checks": checks, "http": counters}, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
