"""Differential deep-scan parity: Python vs Rust on a local fixture server.

Usage (repo root): python scripts/deep_scan_parity.py
Builds a small site (every fallback path exercised), serves it on 127.0.0.1,
runs Python `_deep_scan_and_download_assets` and the Rust driver on identical
copies of the already-mirrored folder, then compares the resulting file trees
byte for byte, the downloaded URL->path maps and the failure reports
(timestamps excluded). Exit code 0 only when everything matches.
"""

import functools
import http.server
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "old"))

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
SERVER = {
    "game/index.html": b'<html><head><link rel="stylesheet" href="css/deep/style.css"></head>'
                       b'<body><script src="js/app.js"></script><img src="img/a.png"></body></html>',
    "game/js/app.js": (b'const a = "img/a.png"; fetch("data/story.json"); import("./chunk-1.js");\n'
                       b'const m = "img/missing.png"; load("/root.js"); const r = "/assets/route.png";\n'
                       b'const s = "snd/theme.mp3"; const html = "pages/err.png";'),
    "game/js/chunk-1.js": b'const late = "img/late.webp";',
    "game/css/deep/style.css": b".x{background:url(bg.png)} .y{background:url(../../img/c.png)}",
    "game/data/story.json": b'{"rows":[{"image":"img/b.png"},{"image":"img/b.png?v=2"}]}',
    "game/img/a.png": PNG,
    "game/img/b.png": PNG + b"b",
    "game/img/c.png": PNG + b"c",
    "game/img/late.webp": b"RIFF\x00\x00\x00\x00WEBPVP8 late",
    "game/bg.png": PNG + b"bg",
    "game/assets/route.png": PNG + b"route",
    "assets/root.js": b"console.log('vite root');",
    "game/snd/theme.mp3": b"ID3\x03 audio",
    "game/pages/err.png": b"<!doctype html><html>not an image</html>",
}
# Already mirrored by the website pass before deep scan runs.
MIRRORED = ["game/index.html", "game/js/app.js", "game/css/deep/style.css"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


# Documented difference (FEATURE_PARITY F-029): Python's fetch_response returns
# None for HTTP errors, so its deep-scan report says "HTTP request failed";
# Rust keeps the real status ("HTTP 404"). Only that text is normalised.
RUST_STATUS = re.compile(rb"Err  : HTTP [1-5][0-9][0-9]$", re.M)


def tree(folder: Path, rust: bool = False) -> dict[str, str]:
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            rel = p.relative_to(folder).as_posix()
            data = p.read_bytes()
            if rel in {"failed_assets.txt", "backup_report.txt"}:
                data = b"\n".join(l for l in data.splitlines() if not l.startswith(b"Generated"))
                if rust:
                    data = RUST_STATUS.sub(b"Err  : HTTP request failed", data)
            out[rel] = data.hex()
    return out


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="deep-parity-"))
    srv = work / "srv"
    for rel, data in SERVER.items():
        (srv / rel).parent.mkdir(parents=True, exist_ok=True)
        (srv / rel).write_bytes(data)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(srv)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/game/"
    home = work / "home"
    home.mkdir()
    # cargo/rustup need the real profile (toolchain config); only the Python
    # downloader is isolated from the user's cache/settings.
    real_env = dict(os.environ)
    os.environ["USERPROFILE"] = os.environ["HOME"] = str(home)
    try:
        sites = {}
        for side in ("py", "rs"):
            site = work / f"site_{side}"
            for rel in MIRRORED:
                target = site / rel.removeprefix("game/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(SERVER[rel])
            sites[side] = site

        from cyoa_downloader_app.download.image_pipeline import _deep_scan_and_download_assets
        # The app imports runtime.surface at startup; it registers the builtin
        # scanner and must be fully imported before worker threads use it.
        import cyoa_downloader_app.runtime.surface  # noqa: F401
        py_downloaded = _deep_scan_and_download_assets(
            folder=str(sites["py"]), base_url=base, output_dir=str(work),
            max_workers=4, exclude_relative_paths={"project.json"},
        )

        env = dict(real_env, DEEP_E2E_DIR=str(sites["rs"]), DEEP_E2E_BASE=base)
        run = subprocess.run(
            ["cargo", "test", "-q", "-p", "cyoa-engine", "--offline", "deep_scan_e2e_runner",
             "--", "--ignored", "--exact", "website::deep_driver::tests::deep_scan_e2e_runner"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        if run.returncode:
            print(run.stdout[-3000:], run.stderr[-3000:])
            return 2
        rs = json.loads(Path(str(sites["rs"]) + ".rust.json").read_text(encoding="utf-8"))

        ok = True
        if dict(sorted(py_downloaded.items())) != rs["downloaded"]:
            ok = False
            print("DOWNLOADED MAP DIFFERS")
            print(" python:", json.dumps(dict(sorted(py_downloaded.items())), indent=1))
            print(" rust:  ", json.dumps(rs["downloaded"], indent=1))
        tp, tr = tree(sites["py"]), tree(sites["rs"], rust=True)
        for rel in sorted(set(tp) | set(tr)):
            if tp.get(rel) != tr.get(rel):
                ok = False
                print("FILE DIFFERS:", rel, "python" if rel in tp else "-", "rust" if rel in tr else "-")
                if rel.endswith(".txt"):
                    print(" py:", bytes.fromhex(tp.get(rel, "")).decode("utf-8", "replace"))
                    print(" rs:", bytes.fromhex(tr.get(rel, "")).decode("utf-8", "replace"))
        print(f"files: {len(tp)} python / {len(tr)} rust; downloaded: {len(py_downloaded)}")
        print("OK deep scan parity" if ok else "DIFF deep scan parity")
        return 0 if ok else 1
    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
