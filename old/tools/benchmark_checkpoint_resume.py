"""Measure native conditional archive continuation on a loopback fixture."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import statistics
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyoa_downloader_app.download import website


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-source", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("cyoa_downloader_app.download._trusted_resume_control", args.control_source)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII=")
    body = png + b"\0" * (1024 * 1024 - len(png))
    digest = hashlib.sha256(body).hexdigest()
    counts = {"requests": 0, "body_bytes": 0, "not_modified": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            counts["requests"] += 1
            time.sleep(0.01)
            if self.headers.get("If-None-Match") == '"fixture-one"':
                counts["not_modified"] += 1
                self.send_response(304)
                self.send_header("ETag", '"fixture-one"')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"fixture-one"')
            self.end_headers()
            self.wfile.write(body)
            counts["body_bytes"] += len(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = {"runs": args.runs, "assets": 16, "asset_bytes": len(body), "before": [], "after": []}
    try:
        with tempfile.TemporaryDirectory(prefix="cyoa-resume-benchmark-") as temporary:
            root = Path(temporary)
            start = f"http://127.0.0.1:{server.server_port}/"
            for run in range(args.runs):
                order = [("before", control), ("after", website)]
                if run % 2:
                    order.reverse()
                for side, module in order:
                    folder = root / f"{run}-{side}"
                    session = requests.Session()
                    session.trust_env = False
                    def fetch(url, session=session, **kwargs):
                        result = session.get(url, headers=kwargs.get("extra_headers"), stream=True, timeout=10)
                        result._cyoa_native_checkpoint = True
                        return result
                    with patch.object(module, "_load_settings", return_value={}), patch.object(module, "fetch_response", fetch), \
                            patch.object(module, "vpn_requirement_satisfied", return_value=True):
                        def instance(resume=False, module=module, folder=folder, side=side):
                            item = module.WebsiteDownloader(start, str(folder), ai_mode="off", archive_strategy="smart")
                            item._checkpoint_context = lambda: "isolated-loopback-profile"
                            if resume and side == "after":
                                item.enable_checkpoint_resume()
                            return item
                        first = instance()
                        try:
                            for index in range(16):
                                first.download_asset(start + f"asset{index}.png?v=one")
                        finally:
                            first.close()
                            first.session.close()
                        for key in counts:
                            counts[key] = 0
                        resumed = instance(resume=True)
                        try:
                            started = time.perf_counter()
                            paths = [Path(resumed.download_asset(start + f"asset{index}.png?v=one")) for index in range(16)]
                            elapsed = time.perf_counter() - started
                        finally:
                            resumed.close()
                            resumed.session.close()
                            session.close()
                        assert all(path.stat().st_size == len(body) and hashlib.sha256(path.read_bytes()).hexdigest() == digest for path in paths)
                        assert counts == {"requests": 16, "body_bytes": 0 if side == "after" else 16 * len(body),
                                          "not_modified": 16 if side == "after" else 0}
                        archive = folder / "verified.zip"
                        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                            for path in paths:
                                zipped.write(path, path.name)
                        with zipfile.ZipFile(archive) as zipped:
                            assert zipped.testzip() is None
                            assert all(hashlib.sha256(zipped.read(name)).hexdigest() == digest for name in zipped.namelist())
                        results[side].append(dict(counts, seconds=elapsed, verified_hashes=len(paths), zip_crc="pass"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    results["median_seconds"] = {side: statistics.median(row["seconds"] for row in results[side]) for side in ("before", "after")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), "utf-8")
    print(json.dumps(results["median_seconds"], indent=2))


if __name__ == "__main__":
    main()
