"""Real Chromium fallback benchmark on local media, with isolated processes."""

from __future__ import annotations

import argparse
import ast
import base64
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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sample(args):
    from playwright.sync_api import Browser, BrowserType

    from cyoa_downloader_app.core.media_io import FilePayload
    from cyoa_downloader_app.network import browser

    logging.disable(logging.CRITICAL)
    browser.vpn_requirement_satisfied = lambda: True
    browser._get_browser_proxy_config = lambda url: {}
    fetch = browser._fetch_headless
    session_type = browser.BrowserFetchSession
    if args.control:
        tree = ast.parse(args.control.read_text(encoding="utf-8"))
        name = "BrowserFetchSession" if args.transport == "session" else "_fetch_headless"
        node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
        namespace = dict(vars(browser))
        namespace["threading"] = threading
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(args.control), "exec"), namespace)
        if args.transport == "session":
            session_type = namespace["BrowserFetchSession"]
        else:
            fetch = namespace["_fetch_headless"]
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII=")
    body = image + b"x" * (args.asset_mib * 1024 * 1024 - len(image))
    expected = hashlib.sha256(body).hexdigest()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionError, OSError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    owner = psutil.Process()
    gc_children = {p.pid for p in owner.children(recursive=True)}
    peak, stop = [0], threading.Event()
    def monitor():
        while not stop.wait(.01):
            try:
                tree = [owner, *owner.children(recursive=True)]
                peak[0] = max(peak[0], sum(p.memory_info().rss for p in tree if p.is_running()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    watcher = threading.Thread(target=monitor, daemon=True)
    counts = {"launches": 0, "peak_browsers": 0}
    live, lock = set(), threading.Lock()
    original_launch, original_close = BrowserType.launch, Browser.close
    def launch(self, *a, **kw):
        result = original_launch(self, *a, **kw)
        with lock:
            live.add(id(result))
            counts["launches"] += 1
            counts["peak_browsers"] = max(counts["peak_browsers"], len(live))
        return result
    def close(self, *a, **kw):
        try:
            return original_close(self, *a, **kw)
        finally:
            with lock:
                live.discard(id(self))
    try:
        with tempfile.TemporaryDirectory(prefix="cyoa-browser-bodies-") as scratch, \
                patch.object(BrowserType, "launch", launch), patch.object(Browser, "close", close):
            session = (session_type() if args.transport == "session" or not args.control else None)
            def task(index):
                url = f"http://127.0.0.1:{server.server_port}/{index}.png"
                if args.transport == "session":
                    result = session.fetch(url, **({} if args.control else {"file_backed": True}))
                    content = result.content if result else None
                else:
                    options = {} if session is None else {"browser_session": session, "spool_dir": scratch}
                    content = fetch(url, reject_error_documents=True, **options)
                if isinstance(content, FilePayload):
                    with open(content.path, "rb") as source:
                        digest = hashlib.file_digest(source, "sha256").hexdigest()
                    os.unlink(content.path)
                else:
                    assert content is not None
                    digest = hashlib.sha256(content).hexdigest()
                assert digest == expected
                return digest
            watcher.start()
            started = time.perf_counter()
            try:
                if args.transport == "session":
                    hashes = [task(index) for index in range(args.assets)]
                else:
                    with ThreadPoolExecutor(max_workers=args.workers) as pool:
                        hashes = list(pool.map(task, range(args.assets)))
            finally:
                if session:
                    session.close()
                seconds = time.perf_counter() - started
                stop.set()
                watcher.join(2)
            assert len(hashes) == args.assets and not live
            assert not list(Path(scratch).iterdir())
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(2)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(p.pid not in gc_children for p in owner.children(recursive=True)):
        time.sleep(.05)
    assert not [p.pid for p in owner.children(recursive=True) if p.pid not in gc_children], "Browser processes remain"
    return {"seconds": seconds, "peak_tree_rss_bytes": peak[0], "verified_assets": len(hashes), **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--assets", type=int, default=12)
    parser.add_argument("--asset-mib", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--transport", choices=("headless", "session"), default="headless")
    args = parser.parse_args()
    if args.sample:
        with tempfile.TemporaryDirectory(prefix="cyoa-browser-profile-") as profile:
            os.environ["USERPROFILE"] = profile
            print(json.dumps(sample(args)))
        return
    samples = []
    for _ in range(args.runs):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--sample", "--assets", str(args.assets),
            "--asset-mib", str(args.asset_mib), "--workers", str(args.workers), "--transport", args.transport,
            *(["--control", str(args.control)] if args.control else [])], capture_output=True, text=True,
            timeout=180, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert result.returncode == 0, result.stderr[-3000:]
        samples.append(json.loads(result.stdout))
    report = {"assets": args.assets, "asset_mib": args.asset_mib, "workers": args.workers, "runs": args.runs,
              "control": str(args.control or ""), "samples": samples,
              "transport": args.transport,
              "medians": {key: statistics.median(row[key] for row in samples) for key in samples[0]}}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["medians"], indent=2))


if __name__ == "__main__":
    main()
