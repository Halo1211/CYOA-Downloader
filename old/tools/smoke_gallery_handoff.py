"""Exercise gallery file handoff through real CLI/extractor processes locally."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This fixture uses a Windows command launcher")
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII=")
    size = 8 * 1024 * 1024
    direct = []
    with tempfile.TemporaryDirectory(prefix="cyoa-gallery-smoke-") as scratch:
        root = Path(scratch)
        calls = root / "calls"
        calls.mkdir()
        helper = root / "extractor.py"
        helper.write_text(
            "import base64, json, sys\nfrom pathlib import Path\nfrom urllib.parse import urlparse\n"
            "directory=Path(sys.argv[sys.argv.index('--destination')+1])\n"
            "index=int(Path(urlparse(sys.argv[-1]).path).stem)\n"
            f"prefix=base64.b64decode({base64.b64encode(png).decode()!r})+str(index).encode().ljust(32,b' ')\n"
            f"remaining={size}-len(prefix)\n"
            "with (directory/'fixture.png').open('wb') as output:\n"
            " output.write(prefix)\n block=bytes(range(256))*256\n"
            " while remaining:\n  count=min(remaining,len(block))\n  output.write(block[:count])\n  remaining-=count\n"
            f"(Path({str(calls)!r})/(directory.name+'.json')).write_text(json.dumps({{'url':sys.argv[-1],'scratch':str(directory)}}))\n",
            encoding="utf-8",
        )
        launcher = root / "extractor.cmd"
        launcher.write_text(f'@echo off\n"{sys.executable}" "{helper}" %*\n', encoding="utf-8")
        project = {"rows": [{"image": f"posts/{i}.png"} for i in range(2)]}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path == "/project.json":
                    status, body = 200, json.dumps(project).encode()
                else:
                    direct.append(self.path)
                    status, body = 404, b"extractor-only fixture"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        checks = []
        command = ([str(args.executable.resolve())] if args.executable else
                   [sys.executable, str(Path(__file__).resolve().parents[1] / "cyoa_downloader.py")])

        def verify(stream, index):
            digest = hashlib.sha256()
            prefix = png + str(index).encode().ljust(32, b" ")
            expected = hashlib.sha256(prefix)
            remaining, block = size - len(prefix), bytes(range(256)) * 256
            while remaining:
                count = min(remaining, len(block))
                expected.update(block[:count])
                remaining -= count
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
            assert digest.digest() == expected.digest()

        try:
            for mode, flags in (("embed", []), ("zip", ["--zip"]), ("both", ["--both"])):
                profile, output = root / ("profile-" + mode), root / mode
                profile.mkdir()
                log = args.report.with_suffix(f".{mode}.log").resolve()
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("wb") as stream:
                    process = subprocess.run([*command, f"http://127.0.0.1:{server.server_port}/project.json", "fixture",
                        "-o", str(output), "--threads", "2", "--proxy-mode", "disabled", "--cloudflare", "off",
                        "--allow-internal-hosts", "--no-ytdlp", "--no-selenium", "--no-deep-scan",
                        "--gallery-dl", "force", "--gallery-dl-path", str(launcher), *flags],
                        cwd=root, env={**os.environ, "USERPROFILE": str(profile), "PYTHONIOENCODING": "utf-8"},
                        stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=90,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                assert process.returncode == 0, log.read_text("utf-8", errors="replace")[-3000:]
                if mode != "zip":
                    embedded = json.loads((output / "fixture.json").read_text("utf-8"))
                    import io
                    for i, row in enumerate(embedded["rows"]):
                        verify(io.BytesIO(base64.b64decode(row["image"].split(",", 1)[1])), i)
                if mode != "embed":
                    archive_path = next(output.glob("*.zip"))
                    with zipfile.ZipFile(archive_path) as archive:
                        assert archive.testzip() is None
                        localized = json.loads(archive.read("project.json"))
                        for i, row in enumerate(localized["rows"]):
                            with archive.open(row["image"]) as stream:
                                verify(stream, i)
                assert not list(output.rglob("*.part")) and not list(output.rglob(".cyoa-media-*"))
                checks.append(mode)
            assert len(list(calls.glob("*.json"))) == 6
            for marker in calls.glob("*.json"):
                assert not Path(json.loads(marker.read_text())["scratch"]).exists()
            assert not any(path.startswith("/posts/") for path in direct), direct
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        report = {"passed": len(checks), "modes": checks, "extractor_processes": 6,
                  "asset_mib": 8, "assets_per_mode": 2, "sha256_verified": True,
                  "extractor": "local file-producing fixture, no external gallery", "executable": str(args.executable or "source")}
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
