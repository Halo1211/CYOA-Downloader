"""Measure complete embed/both EXE jobs, including final JSON validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _main():
    from cyoa_downloader_app.core.json_summary import load_json_summary
    from cyoa_downloader_app.download.orchestrator import _classify_project_image_references
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--assets", type=int, default=16)
    parser.add_argument("--asset-mib", type=int, default=8)
    args = parser.parse_args()
    size = args.asset_mib * 1024 * 1024
    project = json.dumps({"title": "fixture", "rows": [{"image": f"images/{i}.png"} for i in range(args.assets)]}).encode()

    def chunks(index):
        header = b"\x89PNG\r\n\x1a\n" + str(index).encode().ljust(32, b" ")
        yield header
        remaining, block = size - len(header), bytes(range(256)) * 256
        while remaining:
            yield block[:min(remaining, len(block))]
            remaining -= min(remaining, len(block))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/project.json":
                body, mime = (project,), "application/json"
                length = len(project)
            elif path.startswith("/images/") and path.endswith(".png"):
                body, mime, length = chunks(int(path.rsplit("/", 1)[-1][:-4])), "image/png", size
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.end_headers()
            try:
                for block in body:
                    self.wfile.write(block)
            except (ConnectionError, OSError):
                return  # Metadata range probes may close their response early.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    expected = {}
    for i in range(args.assets):
        digest = hashlib.sha256()
        for block in chunks(i):
            digest.update(block)
        expected[f"images/{i}.png"] = digest.hexdigest()
    samples, output_hashes = [], {}
    try:
        with tempfile.TemporaryDirectory(prefix="cyoa-embed-exe-") as scratch:
            for side, exe in (("before", args.before), ("after", args.after)):
                for mode in ("embed", "both"):
                    for run in range(args.runs):
                        profile = Path(scratch) / f"{side}-{mode}-{run}"
                        output = profile / "output"
                        output.mkdir(parents=True)
                        settings = profile / ".cyoa_downloader" / "settings.json"
                        settings.parent.mkdir()
                        settings.write_text(json.dumps({"wait_time": 0, "offline_viewer_json_enabled": False}), encoding="utf-8")
                        env = dict(os.environ, USERPROFILE=str(profile))
                        log = profile / "run.log"
                        start, peak, max_processes = time.perf_counter(), 0, 0
                        with log.open("wb") as handle:
                            process = subprocess.Popen([
                                str(exe.resolve()), f"http://127.0.0.1:{server.server_port}/project.json", "fixture",
                                "-o", str(output), "--threads", "4", "--proxy-mode", "disabled", "--cloudflare", "off",
                                "--allow-internal-hosts", "--no-ytdlp", "--no-selenium", "--no-deep-scan",
                                "--archive-strategy", "classic", *( ["--both"] if mode == "both" else []),
                            ], cwd=profile, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                            owner = psutil.Process(process.pid)
                            while process.poll() is None:
                                try:
                                    tree = [owner, *owner.children(recursive=True)]
                                    peak = max(peak, sum(p.memory_info().rss for p in tree if p.is_running()))
                                    max_processes = max(max_processes, len(tree))
                                except psutil.NoSuchProcess:
                                    pass
                                if time.perf_counter() - start > 90:
                                    process.kill()
                                    process.wait()
                                    raise RuntimeError("EXE fixture exceeded 90 seconds")
                                time.sleep(.01)
                            assert process.returncode == 0, log.read_text(encoding="utf-8", errors="replace")[-3000:]
                        seconds = time.perf_counter() - start
                        embedded = output / "fixture.json"
                        with embedded.open(encoding="utf-8") as source:
                            counts = _classify_project_image_references(load_json_summary(source))
                        assert counts["data"] == counts["total"] == args.assets
                        with embedded.open("rb") as source:
                            digest = hashlib.file_digest(source, "sha256").hexdigest()
                        output_hashes.setdefault(mode, digest)
                        assert output_hashes[mode] == digest, "Embedded JSON differs between builds/runs"
                        if mode == "both":
                            with zipfile.ZipFile(output / "fixture.zip") as archive:
                                assert archive.testzip() is None
                                for name, digest_expected in expected.items():
                                    with archive.open(name) as source:
                                        assert hashlib.file_digest(source, "sha256").hexdigest() == digest_expected
                        assert not list(output.rglob("*.part"))
                        samples.append({"side": side, "mode": mode, "run": run, "seconds": seconds,
                                        "peak_tree_rss_bytes": peak, "max_processes": max_processes})
                        print(f"{side} {mode} {run + 1}: {seconds:.3f}s, peak {peak / 2**20:.2f} MiB", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    summary = {side: {mode: {key: statistics.median(row[key] for row in samples if row["side"] == side and row["mode"] == mode)
                            for key in ("seconds", "peak_tree_rss_bytes", "max_processes")}
                      for mode in ("embed", "both")} for side in ("before", "after")}
    args.report.write_text(json.dumps({"runs": args.runs, "assets": args.assets, "asset_mib": args.asset_mib,
                                      "summary": summary, "samples": samples, "output_sha256": output_hashes}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    with tempfile.TemporaryDirectory(prefix="cyoa-embed-parent-") as profile:
        previous = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = profile
        try:
            _main()
        finally:
            if previous is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = previous


if __name__ == "__main__":
    main()
