"""Export Python `--verify FOLDER [--write-manifest]` behaviour to
fixtures/verify.json (download/package.py verify_output_package /
write_package_manifest).

Each case is a folder tree (relative path -> hex bytes) plus optional steps:
`manifest` writes the sidecar first, `after` then mutates the tree
(path -> hex, or null to delete), `raw_manifest` replaces the sidecar text.
Reports have the absolute folder replaced by FOLDER and the manifest
`created_utc` replaced by CREATED.
Run from the repo root: python fixtures/make_verify.py
"""
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401
from cyoa_downloader_app.download import package

PNG = (b"\x89PNG\r\n\x1a\n" + b"p" * 24).hex()
PROJECT_OK = json.dumps({
    "rows": [{"id": "r1", "title": "Row", "image": "images/hero.png",
              "objects": [{"id": "o", "image": "./images/obj.png?v=2"},
                          {"id": "u", "image": "https://cdn.example/x.png"},
                          {"id": "d", "image": "data:image/png;base64,AAAA"}]}],
    "styling": {"backgroundImage": "bg.png"},
})
INDEX = (b'<!doctype html><html><head><link rel="stylesheet" href="css/app.css">'
         b'<script src="js/app.js"></script></head><body><img src="images/hero.png"></body></html>')

CASES = [
    {
        "name": "good_icc",
        "tree": {
            "project.json": PROJECT_OK.encode().hex(),
            "index.html": INDEX.hex(),
            "css/app.css": b".a{background:url(../images/bg.png)}".hex(),
            "js/app.js": b'fetch("project.json")'.hex(),
            "images/hero.png": PNG,
            "images/obj.png": PNG,
            "images/bg.png": PNG,
            "bg.png": PNG,
        },
        "manifest": True,
    },
    {
        "name": "broken_refs",
        "tree": {
            "project.json": json.dumps({
                "rows": [{"id": "r", "image": "images/Hero.PNG"},
                         {"id": "m", "image": "images/missing.png"},
                         {"id": "b", "image": "solo.png"},
                         {"id": "x", "image": "../escape.png"},
                         {"id": "w", "image": "other/solo.png"}]}).encode().hex(),
            "index.html": (b'<html><head><link rel="icon" href="https://site.example/favicon.ico">'
                           b'<script src="https://cdn.example/lib.js"></script>'
                           b'<link rel="stylesheet" href="css/gone.css"></head><body></body></html>').hex(),
            "images/hero.png": PNG,
            "deep/solo.png": PNG,
            "empty.png": "",
            "failed_assets.txt": "",
            "failed_images.txt": b"# Failed\nhttps://a.example/1.png\nhttps://a.example/1.png\nhttps://a.example/2.png.\n".hex(),
            "failed_urls.txt": b"============\nTotal     : 3\n".hex(),
        },
    },
    {
        "name": "manifest_tampered",
        "tree": {
            "project.json": PROJECT_OK.encode().hex(),
            "images/hero.png": PNG, "images/obj.png": PNG, "bg.png": PNG,
            "nested/cyoa_manifest.json": b"{}".hex(),
        },
        "manifest": True,
        "after": {"images/hero.png": (b"\x89PNG" + b"changed").hex(), "bg.png": None,
                  "added.txt": b"new".hex()},
    },
    {
        "name": "manifest_bad_entries",
        "tree": {"a.txt": b"aaa".hex(), "b.txt": b"bbb".hex()},
        "raw_manifest": json.dumps({
            "manifest_version": 2, "file_count": 9, "created_utc": "2020-01-01T00:00:00Z",
            "files": {
                "a.txt": {"sha256": "0" * 64, "size": 3},
                "b.txt": {"sha256": "nothex", "size": 3},
                "./c.txt": {"sha256": "0" * 64, "size": 1},
                "../d.txt": {"sha256": "0" * 64, "size": 1},
                "e.txt": "string entry",
                "a\\b.txt": {"sha256": "0" * 64, "size": 1},
                "f.txt": {"sha256": "0" * 64, "size": True},
            }}),
    },
    {
        "name": "pure_no_project_bad_manifest",
        "tree": {"index.html": b"<html><body>hi</body></html>".hex()},
        "raw_manifest": "{not json",
    },
    {
        "name": "project_not_object",
        "tree": {"project.json": b"[1, 2]".hex(), "x.bin": b"1".hex()},
    },
    {
        "name": "project_not_project",
        "tree": {"project.json": b'{"hello": "world"}'.hex()},
    },
    {
        "name": "many_zero_bytes",
        "tree": {f"z/{i:02}.png": "" for i in range(28)},
    },
    {
        "name": "empty_folder",
        "tree": {},
    },
]


def build(root, tree):
    for rel, hx in tree.items():
        p = os.path.join(root, *rel.split("/"))
        if hx is None:
            os.remove(p)
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(bytes.fromhex(hx))


def norm(text, root):
    text = text.replace(root, "FOLDER")
    return re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "CREATED", text)


out = []
for case in CASES:
    tmp = tempfile.mkdtemp(prefix="verify-")
    root = os.path.join(tmp, "pkg")
    os.makedirs(root)
    try:
        build(root, case["tree"])
        result = {}
        if case.get("manifest"):
            ok, msg = package.write_package_manifest(root)
            result["manifest_ok"] = ok
            result["manifest_msg"] = msg
            with open(os.path.join(root, "cyoa_manifest.json"), encoding="utf-8") as f:
                result["manifest_text"] = norm(f.read(), root)
        if "raw_manifest" in case:
            with open(os.path.join(root, "cyoa_manifest.json"), "w", encoding="utf-8") as f:
                f.write(case["raw_manifest"])
        if "after" in case:
            build(root, case["after"])
        ok, report = package.verify_output_package(root)
        result["verify_ok"] = ok
        result["report"] = norm(report, root)
        out.append(dict(case, expected=result))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

missing_ok, missing_report = package.verify_output_package(os.path.join(tempfile.gettempdir(), "no-such-verify-dir"))
wm_ok, wm_msg = package.write_package_manifest(os.path.join(tempfile.gettempdir(), "no-such-verify-dir"))
doc = {
    "cases": out,
    "missing_folder": {"verify_ok": missing_ok,
                       "report": missing_report.replace(os.path.join(tempfile.gettempdir(), "no-such-verify-dir"), "FOLDER"),
                       "manifest_ok": wm_ok,
                       "manifest_msg": wm_msg.replace(os.path.join(tempfile.gettempdir(), "no-such-verify-dir"), "FOLDER")},
}
with open("fixtures/verify.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok", len(out))
