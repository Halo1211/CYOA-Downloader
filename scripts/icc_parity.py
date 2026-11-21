"""End-to-end ICC mode parity: `cyoa_downloader.py URL --icc-folder` vs Rust CLI.

Usage (repo root): python scripts/icc_parity.py
Serves a small ICC viewer site on 127.0.0.1, runs both CLIs with an isolated
profile, and compares the produced site folders byte for byte (log file and
timestamp lines excluded; the documented deep-scan failure-status wording is
normalised). Then repeats with `--icc` and compares the `_site.zip` entries.
Exit code 0 only when everything matches.
"""

import functools
import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 16
PROJECT = (
    '{"rows":[{"id":"r1","title":"Row","image":"img/hero.png","objects":['
    '{"id":"o1","title":"Obj","image":"https://HOST/game/img/obj.png"},'
    '{"id":"o2","title":"Gone","image":"img/gone.png"}]}],'
    '"styling":{"customCss":"body{background:url(img/bg.png)} @font-face{src:url(fonts/p.woff2)}"},'
    '"font":"http://HOST/game/fonts/d.ttf?v=2","version":"1.2"}'
)
SITE = {
    "game/index.html": b'<!doctype html><html><head><meta charset="utf-8"><title>T</title>'
                       b'<link rel="stylesheet" href="css/app.css"></head><body>'
                       b'<div id="app"></div><script src="js/app.js"></script>'
                       b'<a href="ch1/">One</a><a href="ch1">One again</a><a href="data.json">Data</a>'
                       b'<a href="login">Login</a></body></html>',
    "game/ch1/index.html": b'<html><body><h1>One</h1><a href="../ch2/">Two</a><a href="../">Home</a>'
                           b'<img src="../img/ui.png"></body></html>',
    "game/ch2/index.html": b'<html><body><h1>Two</h1><a href="../ch1/#top">Back</a></body></html>',
    "game/data.json": b'{"k": 1}',
    "game/css/app.css": b"@import url(\"fonts.css\"); .h{background:url(../img/ui.png)}",
    "game/css/fonts.css": b"@font-face{font-family:F;src:url(../fonts/f.woff2) format('woff2')}",
    "game/js/app.js": b'fetch("project.json"); const extra = "img/extra.webp"; import("./lazy-1.js");',
    "game/js/lazy-1.js": b'const deep = "img/deep.png";',
    "game/img/hero.png": PNG + b"hero",
    "game/img/obj.png": PNG + b"obj",
    "game/img/bg.png": PNG + b"bg",
    "game/img/ui.png": PNG + b"ui",
    "game/img/extra.webp": b"RIFF\x00\x00\x00\x00WEBPVP8 x",
    "game/img/deep.png": PNG + b"deep",
    "game/fonts/f.woff2": b"wOF2font",
    "game/fonts/p.woff2": b"wOF2project",
    "game/fonts/d.ttf": b"\x00\x01\x00\x00ttf",
}
SKIP = {"cyoa_downloader.log"}
RUST_STATUS = re.compile(rb"Err  : HTTP [1-5][0-9][0-9]$", re.M)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def sort_downloaded_block(data: bytes) -> bytes:
    """Python orders deep-scan registrations by set iteration (randomised
    string hashing per process), so compare that block as a sorted list."""
    lines = data.replace(b"\r\n", b"\n").split(b"\n")
    try:
        start = lines.index(b"Downloaded files:") + 1
    except ValueError:
        return data
    end = start
    while end < len(lines) and lines[end].startswith(b"  "):
        end += 1
    lines[start:end] = sorted(lines[start:end])
    return b"\n".join(lines)


def sort_deep_scan_entries(data: bytes) -> bytes:
    """Failure reports list entries in completion order on both sides
    (Python thread pool / randomised deep-scan sets); compare the
    "[n] <kind>" entries sorted and renumbered."""
    text = data.replace(b"\r\n", b"\n")
    if b"Asset Report" not in text:
        return data
    head, _, body = text.partition(b"\n\n[1] ")
    if not body:
        return data
    blocks = (b"[1] " + body).strip(b"\n").split(b"\n\n")
    stripped = sorted(b.split(b"] ", 1)[1] if b"] " in b else b for b in blocks)
    renumbered = [b"[%d] " % (i + 1) + s for i, s in enumerate(stripped)]
    return head + b"\n\n" + b"\n\n".join(renumbered) + b"\n"


def sort_failed_lines(data: bytes) -> bytes:
    """failed_images.txt rows follow completion order on both sides."""
    lines = data.replace(b"\r\n", b"\n").split(b"\n")
    head = [l for l in lines if l.startswith(b"#") or not l.strip()]
    rows = sorted(l for l in lines if l.strip() and not l.startswith(b"#"))
    return b"\n".join(head + rows)


def normalise(rel: str, data: bytes, rust: bool) -> bytes:
    if rel == "backup_report.txt":
        data = sort_downloaded_block(data)
    if rel == "failed_assets.txt":
        data = sort_deep_scan_entries(data)
    if rel == "failed_images.txt":
        data = sort_failed_lines(data)
    if rel.endswith(".txt"):
        data = b"\n".join(
            l for l in data.splitlines()
            if not l.startswith((b"Generated", b"# Generated", b"# --- "))
        )
        if rust:
            data = RUST_STATUS.sub(b"Err  : HTTP request failed", data)
    return data


def folder_tree(folder: Path, rust: bool) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): normalise(p.relative_to(folder).as_posix(), p.read_bytes(), rust)
        for p in sorted(folder.rglob("*")) if p.is_file() and p.name not in SKIP
    }


def zip_tree(path: Path, rust: bool) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: normalise(n, z.read(n), rust) for n in sorted(z.namelist()) if not n.endswith("/")}


def compare(label: str, py: dict, rs: dict) -> bool:
    ok = True
    for rel in sorted(set(py) | set(rs)):
        if py.get(rel) != rs.get(rel):
            ok = False
            print(f"  {label} DIFF {rel}: python={'yes' if rel in py else 'no'} rust={'yes' if rel in rs else 'no'}")
            if rel in py and rel in rs and len(py[rel]) < 4000:
                print("    py:", py[rel][:1500].decode("utf-8", "replace"))
                print("    rs:", rs[rel][:1500].decode("utf-8", "replace"))
    print(f"{'OK  ' if ok else 'DIFF'} {label}: {len(py)} python / {len(rs)} rust files")
    print("      files:", ", ".join(sorted(py)))
    return ok


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="icc-parity-"))
    srv = work / "srv"
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(srv)))
    host = f"127.0.0.1:{httpd.server_address[1]}"
    for rel, data in {**SITE, "game/project.json": PROJECT.replace("HOST", host).encode()}.items():
        (srv / rel).parent.mkdir(parents=True, exist_ok=True)
        (srv / rel).write_bytes(data)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{host}/game/"
    home = work / "home"
    home.mkdir()
    iso = dict(os.environ, USERPROFILE=str(home), HOME=str(home))
    try:
        # ICC_GUI=1: the Rust side is the desktop app driven through its real
        # Add URL + Download All handlers (CYOA_DESKTOP_E2E), not the CLI.
        gui = os.environ.get("ICC_GUI") == "1"
        pkg = "cyoa-desktop" if gui else "cyoa-cli"
        subprocess.run(["cargo", "build", "-q", "-p", pkg, "--offline"], cwd=ROOT, check=True)
        rust_bin = ROOT / "target" / "debug" / ("cyoa-desktop" if gui else "cyoa-downloader")
        gui_modes = {"--icc-folder": "IccFolder", "--icc": "IccZip", "--zip": "Zip",
                     "--pure-website-folder": "PureFolder", "--pure-website": "PureZip"}
        ok = True
        # (strategy, flag, path). Pure Auto from ch1/ profiles as Classic; from
        # /game/ it profiles as Browser (added below when Playwright exists).
        runs = [("auto", "--icc-folder", ""), ("auto", "--icc", ""), ("classic", "--icc-folder", ""),
                ("smart", "--icc-folder", ""), ("auto", "--pure-website-folder", "ch1/"),
                ("classic", "--pure-website-folder", ""), ("smart", "--pure-website", ""),
                # --fonts: ICC (project fonts), pure (viewer fonts), plain ZIP.
                ("auto", "--icc-folder", "", "--fonts"),
                ("classic", "--pure-website-folder", "", "--fonts"),
                ("auto", "--zip", "", "--fonts"),
                # CLI overrides of the saved archive settings.
                ("auto", "--icc-folder", "", "--archive-strategy", "smart", "--archive-max-pages", "2"),
                # SSRF screen off: the https://HOST asset is attempted, not blocked.
                ("auto", "--icc-folder", "", "--allow-internal-hosts")]
        # F-028 Browser strategy (Python Playwright vs Rust CDP), including
        # Pure Auto from /game/ which profiles as Browser. Needs Playwright
        # on the Python side and Chrome/Edge for Rust.
        import importlib.util
        if importlib.util.find_spec("playwright") is not None:
            runs += [("browser", "--pure-website-folder", ""), ("browser", "--icc-folder", ""),
                     ("auto", "--pure-website-folder", "")]
        else:
            print("SKIP browser strategy cases: Python Playwright is not installed")
        if gui:
            runs = [r for r in runs if len(r) == 3]
        for strategy, flag, sub, *extra in runs:
            # Python reads settings.json `archive_strategy` (default auto);
            # classic also runs the recovery pass and the deep scan.
            settings = home / ".cyoa_downloader" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            # schema >= 2: older files have a legacy "classic" migrated to auto.
            settings.write_text(
                '{"_meta": {"schema_version": 4}, "archive_strategy": "%s"}' % strategy,
                encoding="utf-8",
            )
            outs = {}
            for side in ("py", "rs"):
                out = work / f"{side}{flag}-{strategy}-{sub.strip('/')}{''.join(extra)}"
                if side == "rs" and gui:
                    out.mkdir(parents=True, exist_ok=True)
                    env = dict(iso, CYOA_DESKTOP_E2E=f"{url + sub}|{out}|{gui_modes[flag]}|game")
                    run = subprocess.run([str(rust_bin)], cwd=ROOT, env=env, capture_output=True,
                                         text=True, encoding="utf-8", errors="replace", timeout=600)
                    status = (out / "gui_e2e_status.txt")
                    st = status.read_text(encoding="utf-8") if status.exists() else "no status"
                    status.unlink(missing_ok=True)
                    if not st.startswith("Done"):
                        print(f"rs GUI {flag} [{strategy}] job: {st}")
                        return 2
                    outs[side] = out
                    continue
                cmd = ([sys.executable, str(ROOT / "old" / "cyoa_downloader.py")] if side == "py" else [str(rust_bin)])
                cmd += [url + sub, "game", flag, "-o", str(out), "-w", "0", *extra]
                if gui:
                    # The GUI "Fonts" checkbox is on by default (Python and Rust).
                    cmd.append("--fonts")
                run = subprocess.run(cmd, cwd=ROOT, env=iso, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace")
                if run.returncode:
                    print(f"{side} {flag} exit {run.returncode}\n{run.stderr[-2000:]}")
                    return 2
                outs[side] = out
            label = f"{flag} {' '.join(extra)} [{strategy}] {sub}".rstrip()
            if flag == "--zip":
                ok &= compare(label, zip_tree(outs["py"] / "game.zip", False),
                              zip_tree(outs["rs"] / "game.zip", True))
            elif flag.endswith("-folder"):
                ok &= compare(label, folder_tree(outs["py"] / "game", False),
                              folder_tree(outs["rs"] / "game", True))
            else:
                ok &= compare(label, zip_tree(outs["py"] / "game_site.zip", False),
                              zip_tree(outs["rs"] / "game_site.zip", True))
                for side in ("py", "rs"):
                    if (outs[side] / "game").exists():
                        print(f"  {side}: site folder should be removed after zipping")
                        ok = False
        print("OK icc parity" if ok else "DIFF icc parity")
        return 0 if ok else 1
    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
