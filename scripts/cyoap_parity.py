"""End-to-end CYOAP Vue parity: Python CLI vs Rust CLI on a local CYOAP site.

Usage (repo root): python scripts/cyoap_parity.py [--python-only]
Runs `--cyoap-vue-folder` and `--icc-folder` (engine auto: the CYOAP probe
wins) with one worker (-t 1, deterministic order) and compares the outputs
byte for byte using the normalisation rules of icc_parity.py.
"""

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from icc_parity import Quiet, compare, folder_tree  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"c" * 12
SITE = {
    "cy/index.html": b'<!doctype html><html><head><meta charset="utf-8"><title>CYOAP</title>'
                     b'<link rel="stylesheet" href="css/app.css"><link rel="icon" href="img/logo.png">'
                     b'</head><body><div id="app"></div><script src="js/app.js"></script>'
                     b'<img src="img/logo.png"></body></html>',
    "cy/css/app.css": b'@import "b.css"; @font-face{font-family:F;src:url(../fonts/f.woff2)}',
    "cy/css/b.css": b".b{background:url('../img/bg.png')}",
    "cy/js/app.js": b"console.log('cyoap');",
    "cy/fonts/f.woff2": b"wOF2font",
    "cy/img/logo.png": PNG + b"logo",
    "cy/img/bg.png": PNG + b"bg",
    "cy/dist/platform.json": json.dumps({
        "title": "T", "image": "cover.webp123", "desc": "see extra.png", "bgm": "theme.mp3",
    }).encode(),
    "cy/dist/nodes/list.json": json.dumps(["n1.json", "n2.json", " ", 5]).encode(),
    "cy/dist/nodes/n1.json": json.dumps({"choices": [{"image": "pic.png"}, {"image": "/cy/abs.png"},
                                                     {"image": "missing.png"}]}).encode(),
    "cy/dist/images/cover.webp": b"RIFF\x00\x00\x00\x00WEBPcover",
    "cy/dist/images/pic.png": PNG + b"pic",
    "cy/dist/images/extra.png": PNG + b"extra",
    "cy/abs.png": PNG + b"abs",
    "cy/dist/audio/theme.mp3": b"ID3audio",
}


def main() -> int:
    python_only = "--python-only" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="cyoap-parity-"))
    srv = work / "srv"
    for rel, data in SITE.items():
        (srv / rel).parent.mkdir(parents=True, exist_ok=True)
        (srv / rel).write_bytes(data)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(srv)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/cy/"
    home = work / "home"
    home.mkdir()
    iso = dict(os.environ, USERPROFILE=str(home), HOME=str(home))
    # ICC_GUI=1: the Rust side is the desktop app (CYOA_DESKTOP_E2E handlers).
    gui = os.environ.get("ICC_GUI") == "1"
    try:
        if not python_only:
            subprocess.run(["cargo", "build", "-q", "-p", "cyoa-desktop" if gui else "cyoa-cli", "--offline"],
                           cwd=ROOT, check=True)
        rust_bin = ROOT / "target" / "debug" / ("cyoa-desktop" if gui else "cyoa-downloader")
        gui_modes = {"--cyoap-vue-folder": "CyoapFolder", "--icc-folder": "IccFolder"}
        ok = True
        for flag in ("--cyoap-vue-folder", "--icc-folder"):
            outs = {}
            sides = ("py",) if python_only else ("py", "rs")
            for side in sides:
                out = work / f"{side}{flag}"
                if side == "rs" and gui:
                    out.mkdir(parents=True, exist_ok=True)
                    env = dict(iso, CYOA_DESKTOP_E2E=f"{url}|{out}|{gui_modes[flag]}|game")
                    subprocess.run([str(rust_bin)], cwd=ROOT, env=env, capture_output=True, timeout=600)
                    st_file = out / "gui_e2e_status.txt"
                    st = st_file.read_text(encoding="utf-8") if st_file.exists() else "no status"
                    st_file.unlink(missing_ok=True)
                    if not st.startswith("Done"):
                        print(f"rs GUI {flag} job: {st}")
                        return 2
                    outs[side] = out
                    continue
                cmd = ([sys.executable, str(ROOT / "old" / "cyoa_downloader.py")] if side == "py" else [str(rust_bin)])
                cmd += [url, "game", flag, "-o", str(out), "-w", "0", "-t", "1"]
                if gui:
                    cmd.append("--fonts")
                run = subprocess.run(cmd, cwd=ROOT, env=iso, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace")
                if run.returncode:
                    print(f"{side} {flag} exit {run.returncode}\n{run.stderr[-2000:]}")
                    return 2
                outs[side] = out
            if python_only:
                tree = folder_tree(outs["py"] / "game", False)
                print(flag, sorted(tree))
                for name in ("backup_report.txt", "failed_assets.txt", "dist/platform.json", "index.html"):
                    if name in tree:
                        print(f"--- {name}\n{tree[name].decode('utf-8', 'replace')}")
                continue
            ok &= compare(flag, folder_tree(outs["py"] / "game", False), folder_tree(outs["rs"] / "game", True))
        if not python_only:
            print("OK cyoap parity" if ok else "DIFF cyoap parity")
        return 0 if ok else 1
    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
