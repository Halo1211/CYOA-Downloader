"""Exercise a packaged Windows EXE using isolated profiles and a local server."""

from __future__ import annotations

import argparse
import base64
import functools
import json
import os
import subprocess
import tempfile
import threading
import time
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-self-test", help="Require a named PASS case in frozen self-test output")
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    if os.name != "nt":
        parser.error("This check requires a Windows executable on Windows")
    checks = []

    with tempfile.TemporaryDirectory(prefix="cyoa-frozen-smoke-") as scratch:
        root = Path(scratch)
        site = root / "site"
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII="
        )
        (site / "images").mkdir(parents=True)
        (site / "images" / "hero.png").write_bytes(png)
        (site / "project.json").write_text(json.dumps({
            "title": "Offline fixture", "rows": [{"objects": [{"image": "images/hero.png"}]}],
        }), encoding="utf-8")
        html = ('<!doctype html><html><head><title>Fixture</title><link rel="stylesheet" href="style.css">'
                '</head><body><img src="images/hero.png"><script src="app.js"></script></body></html>')
        (site / "index.html").write_text(html, encoding="utf-8")
        (site / "style.css").write_text("body{background:#eee}", encoding="utf-8")
        (site / "app.js").write_text('fetch("project.json").then(r=>r.json()).then(p=>window.project=p);', encoding="utf-8")
        cyoap = site / "cyoap"
        (cyoap / "dist" / "nodes").mkdir(parents=True)
        (cyoap / "dist" / "images").mkdir()
        (cyoap / "dist" / "images" / "hero.png").write_bytes(png)
        (cyoap / "dist" / "platform.json").write_text('{"title":"Fixture","image":"hero.png"}', encoding="utf-8")
        (cyoap / "dist" / "nodes" / "list.json").write_text('["chapter.json"]', encoding="utf-8")
        (cyoap / "dist" / "nodes" / "chapter.json").write_text('{"image":"hero.png"}', encoding="utf-8")
        (cyoap / "index.html").write_text('<!doctype html><img src="dist/images/hero.png">', encoding="utf-8")
        story = site / "story"
        chapter = story / "chapters" / "one"
        (chapter / "next").mkdir(parents=True)
        (story / "index.html").write_text('<base href="chapters/"><a href="one">One</a>', encoding="utf-8")
        (chapter / "index.html").write_text(
            '<img src="hero.png"><script src="app.js"></script><a href="next#end">Next</a>'
            '<img srcset="data:image/png;base64,AAAA 1x,hero.png?crop=1,2,3,4 2x">'
            '<svg><use href="icons%23set%25.svg#mark"></use></svg><iframe src="frame/"></iframe>'
            '<iframe src="document.pdf"></iframe><iframe src="xhtml-frame"></iframe>', encoding="utf-8",
        )
        (chapter / "hero.png").write_bytes(png)
        (chapter / "app.js").write_text("window.storyLoaded = true;", encoding="utf-8")
        (chapter / "icons#set%.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><symbol id="mark"><rect width="10" height="10"/></symbol></svg>',
            encoding="utf-8",
        )
        (chapter / "frame").mkdir()
        (chapter / "frame" / "index.html").write_text('<h2>Offline frame</h2><img src="../hero.png">', encoding="utf-8")
        pdf = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n<stream>raw&data</stream>\n%%EOF\n"
        (chapter / "document.pdf").write_bytes(pdf)
        (chapter / "xhtml-frame").write_text('<h2>XHTML frame</h2>', encoding="utf-8")
        (chapter / "next" / "index.html").write_text('<h1 id="end">End</h1>', encoding="utf-8")
        deep = site / "deep"
        deep.mkdir()
        (deep / "index.html").write_text(
            '<!doctype html><html><head></head><body><script src="main.abcdef12.js"></script></body></html>', encoding="utf-8",
        )
        (deep / "main.abcdef12.js").write_text(
            'const image = new Image(); image.src="hidden%23icon.png"; document.body.appendChild(image);'
            'const css=document.createElement("link");css.rel="stylesheet";css.href="redirect.css";document.head.appendChild(css);', encoding="utf-8",
        )
        (deep / "hidden#icon.png").write_bytes(png)
        remote = site / "remote/themes"
        remote.mkdir(parents=True)
        (remote / "main.css").write_text('@import "nested.css";body{background:url(/remote/pattern.png)}', encoding="utf-8")
        (remote / "nested.css").write_text('body{background-image:url(nested_asset.png)}', encoding="utf-8")
        (remote / "nested_asset.png").write_bytes(png)
        (site / "remote/pattern.png").write_bytes(png)
        attempts = []

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *values):
                pass

            def guess_type(self, path):
                if path.endswith("xhtml-frame"):
                    return "application/xhtml+xml"
                return super().guess_type(path)

            def do_GET(self):
                if self.path == "/deep/redirect.css":
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{self.server.server_port}/remote/themes/main.css")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.endswith("/hero.png"):
                    attempts.append(self.path)
                    if len(attempts) == 1:
                        self.send_response(503)
                        self.send_header("Retry-After", "0")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                super().do_GET()

        def run(name: str, flags: list[str]) -> None:
            profile = root / ("profile_" + name)
            profile.mkdir(exist_ok=True)
            env = os.environ.copy()
            env["USERPROFILE"] = str(profile)
            env["PYTHONIOENCODING"] = "utf-8"
            started = time.perf_counter()
            result = subprocess.run(
                [str(executable), *flags], cwd=root, env=env,
                capture_output=True, timeout=90, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                raise RuntimeError(f"{name}: exit code {result.returncode}: {result.stderr[-2000:]!r}")
            if name == "self_test" and args.require_self_test:
                marker = ("PASS  " + args.require_self_test).encode("utf-8")
                assert marker in result.stdout, f"self_test: required case did not pass: {args.require_self_test}"
            checks.append({"name": name, "seconds": round(time.perf_counter() - started, 3)})

        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(site)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for name, flag in (("version", "--version"), ("help", "--help"),
                               ("dependencies", "--dependency-check"), ("self_test", "--self-test")):
                run(name, [flag])
                print(name + " PASS", flush=True)
            modes = (
                ("embed", []), ("zip", ["--zip"]), ("both", ["--both"]),
                ("icc_folder", ["--icc-folder"]), ("icc_zip", ["--icc"]),
                ("website_folder", ["--pure-website-folder"]), ("website_zip", ["--pure-website"]),
                ("cyoap_folder", ["--cyoap-vue-folder"]), ("cyoap_zip", ["--cyoap-vue-website"]),
            )
            for name, flags in modes:
                attempts.clear()
                output = root / name
                route = "/cyoap/" if name.startswith("cyoap_") else "/"
                run(name, [
                    f"http://127.0.0.1:{server.server_port}{route}", "fixture", "-o", str(output),
                    "--threads", "3", "--proxy-mode", "disabled", "--cloudflare", "off",
                    "--allow-internal-hosts", "--no-ytdlp", "--no-selenium", "--no-deep-scan",
                    "--archive-strategy", "classic", *flags,
                ])
                files = [path for path in output.rglob("*") if path.is_file()]
                if name in {"embed", "both"}:
                    assert any(
                        '"image":"data:image/' in path.read_text(encoding="utf-8").replace(" ", "")
                        for path in files if path.suffix == ".json"
                    ), f"{name}: embedded image is missing"
                if name in {"zip", "both", "icc_zip", "website_zip", "cyoap_zip"}:
                    archives = [path for path in files if path.suffix == ".zip"]
                    assert archives, f"{name}: ZIP is missing"
                    with zipfile.ZipFile(archives[0]) as archive:
                        assert archive.testzip() is None, f"{name}: ZIP checksum failure"
                        assert any(entry.endswith(".png") for entry in archive.namelist()), f"{name}: PNG is missing"
                        if name == "cyoap_zip":
                            assert any(entry.endswith("dist/platform.json") for entry in archive.namelist())
                        elif name != "website_zip":
                            assert "project.json" in archive.namelist()
                if name.endswith("_folder"):
                    assert any(path.name == "index.html" for path in files), f"{name}: entry page is missing"
                    assert any(path.name == "hero.png" for path in files), f"{name}: image is missing"
                    if name == "cyoap_folder":
                        assert any(path.name == "platform.json" for path in files)
                assert len(attempts) >= 2, f"{name}: transient HTTP 503 did not recover"
                print(name + " PASS (HTTP 503 recovered)", flush=True)
            attempts.clear()
            output = root / "smart_archive"
            run("smart_archive", [
                f"http://127.0.0.1:{server.server_port}/story", "fixture", "-o", str(output),
                "--pure-website-folder", "--archive-strategy", "smart", "--archive-max-pages", "5",
                "--threads", "3", "--proxy-mode", "disabled", "--cloudflare", "off",
                "--allow-internal-hosts", "--no-ytdlp", "--no-selenium", "--no-deep-scan",
            ])
            manifests = list(output.rglob("archive_manifest.json"))
            assert len(manifests) == 1, "smart_archive: manifest is missing"
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            assert len(manifest["pages"]) == 3 and not manifest["route_failures"], manifest
            archive_root = manifests[0].parent
            assert 'href="routes/chapters/one/index.html"' in (archive_root / "index.html").read_text(encoding="utf-8")
            chapter_file = archive_root / "routes/chapters/one/index.html"
            assert 'href="next/index.html#end"' in chapter_file.read_text(encoding="utf-8")
            assert "icons%23set%25.svg#mark" in chapter_file.read_text(encoding="utf-8")
            assert (archive_root / "chapters/one/frame/index.html").is_file(), "smart_archive: nested iframe is missing"
            assert (archive_root / "chapters/one/icons#set%.svg").is_file(), "smart_archive: SVG sprite is missing"
            assert (archive_root / "chapters/one/xhtml-frame.html").is_file(), "smart_archive: XHTML frame is missing"
            assert (archive_root / "chapters/one/document.pdf").read_bytes() == pdf, "smart_archive: PDF bytes changed"
            assert any(archive_root.rglob("hero.png")), "smart_archive: image is missing"
            assert any(archive_root.rglob("hero_*.png")), "smart_archive: responsive variant is missing"
            assert "hero.png?crop=" not in chapter_file.read_text(encoding="utf-8"), "smart_archive: responsive image stayed online"
            assert len(attempts) >= 2, "smart_archive: transient HTTP 503 did not recover"
            print("smart_archive PASS (redirects, HTML base, 3 routes, iframe, XHTML, SVG, PDF, HTTP 503 recovery)", flush=True)
            output = root / "deep_archive"
            run("deep_archive", [
                f"http://127.0.0.1:{server.server_port}/deep/", "fixture", "-o", str(output),
                "--pure-website-folder", "--archive-strategy", "classic", "--threads", "2",
                "--proxy-mode", "disabled", "--cloudflare", "off", "--allow-internal-hosts",
                "--no-ytdlp", "--no-selenium",
            ])
            images = list(output.rglob("hidden#icon.png"))
            assert len(images) == 1 and images[0].read_bytes() == png, "deep_archive: literal image filename is missing"
            assert not any(output.rglob("hidden")), "deep_archive: filename was truncated at #"
            assert any(output.rglob("nested_asset*.png")), "deep_archive: redirected stylesheet lost a nested image"
            assert any(output.rglob("pattern*.png")), "deep_archive: cross-origin root-relative CSS image is missing"
            print("deep_archive PASS (bundle discovery, literal filename, redirected CDN CSS dependencies)", flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    report = {"executable": str(executable), "checks_passed": len(checks), "checks": checks}
    if args.require_self_test:
        report["required_self_test"] = args.require_self_test
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Result: {len(checks)} checks passed", flush=True)


if __name__ == "__main__":
    main()
