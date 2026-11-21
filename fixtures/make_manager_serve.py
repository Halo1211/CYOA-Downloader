"""Export Python cyoa_manager.prepare_cyoa_manager_serve_folder behaviour to
fixtures/manager_serve.json. Run from the repo root:
    python fixtures/make_manager_serve.py
Temporary HOME and tempdir; network replaced by a fake fetcher. Cache-path
hash segments are masked as <key>/<sig> (they depend on absolute paths).
"""
import base64
import io
import json
import os
import re
import sys
import tempfile
import zipfile

home = tempfile.mkdtemp(prefix="cyoa_mgr_serve_")
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
import cyoa_downloader_app.network.fetch as netfetch  # noqa: E402
from cyoa_downloader_app.integrations import cyoa_manager as mgr  # noqa: E402
from cyoa_downloader_app.integrations.offline_viewers import registry as reg  # noqa: E402

TMP = os.path.join(home, "tmp")
os.makedirs(TMP)
tempfile.tempdir = TMP
WEB = {"https://cdn.example.com/v.js": (200, "application/javascript", b"var v=1;")}
requested = []


class Resp:
    def __init__(self, s, c, b):
        self.status_code, self.headers, self.content = s, {"Content-Type": c}, b

    def close(self):
        pass


def fake_fetch(url, timeout=20, extra_headers=None, **_):
    requested.append(url)
    return Resp(*WEB[url]) if url in WEB else None


netfetch.fetch_response = fake_fetch
# Bundled registration is not ported: keep it a no-op on the Python side too.
reg._auto_register_bundled_viewers = lambda: None
import cyoa_downloader_app.integrations.offline_viewers.registry as _r  # noqa: E402
_r._auto_register_bundled_viewers = lambda: None

os.makedirs(reg._VIEWERS_DIR, exist_ok=True)
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("index.html", '<html><head><script src="https://cdn.example.com/v.js"></script></head>'
                             '<body><div id="projectSize">0</div></body></html>')
    z.writestr("js/app.js", "/*! Delete and replace this part with your project */{s:1};run()")
VIEWER_ZIP = buf.getvalue()
with open(os.path.join(reg._VIEWERS_DIR, "orig.zip"), "wb") as f:
    f.write(VIEWER_ZIP)
MANIFEST = {"orig": {"name": "Viewer 1.8 [Original]", "zip_filename": "orig.zip", "viewer_type": "icc_original"}}
with open(reg._VIEWERS_MANIFEST, "w", encoding="utf-8") as f:
    json.dump(MANIFEST, f)

PROJ = os.path.join(home, "lib", "My CYOA")
FILES = {
    "project.json": "﻿" + json.dumps({"rows": [{"id": "r", "image": "images/a.png"}],
                                          "pointTypes": [], "styling": {}}, ensure_ascii=False)
                    .replace(",", ",\r\n"),
    "images/a.png": "A",
    "images/sub/b.png": "B",
    "audio/s.mp3": "S",
    "cover.webp": "W",
    "notes.txt": "n",
}
for rel, data in FILES.items():
    p = os.path.join(PROJ, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(data)
OTHER = os.path.join(home, "lib", "Served")
os.makedirs(OTHER)
open(os.path.join(OTHER, "index.html"), "w").write("x")
open(os.path.join(OTHER, "project.json"), "w").write('{"rows": []}')
open(os.path.join(home, "lib", "bad.json"), "w").write('{"no": 1}')
open(os.path.join(home, "lib", "bad.md"), "w").write('{"rows": []}')

MASK = re.compile(r"cyoa-downloader-manager-preview/[0-9a-f]{16}/[0-9a-f]{16}")


def rel(p):
    return MASK.sub("cyoa-downloader-manager-preview/<key>/<sig>",
                    os.path.relpath(p, home).replace("\\", "/"))


def tmp_tree():
    out = {}
    for base, _d, files in os.walk(TMP):
        for fn in files:
            p = os.path.join(base, fn)
            data = open(p, "rb").read()
            try:
                out[rel(p)] = {"text": data.decode("utf-8")}
            except UnicodeDecodeError:
                out[rel(p)] = {"b64": base64.b64encode(data).decode()}
    return out


steps = []
for name, target in [("build", os.path.join(PROJ, "project.json")),
                     ("cached", os.path.join(PROJ, "project.json")),
                     ("served_in_place", os.path.join(OTHER, "project.json")),
                     ("no_rows", os.path.join(home, "lib", "bad.json")),
                     ("bad_suffix", os.path.join(home, "lib", "bad.md"))]:
    del requested[:]
    try:
        res = {"folder": rel(mgr.prepare_cyoa_manager_serve_folder(target))}
    except (OSError, TypeError, ValueError) as e:
        res = {"error": type(e).__name__, "message": str(e)}
    steps.append({"name": name, "target": os.path.relpath(target, home).replace("\\", "/"),
                  "result": res, "requested": list(requested), "tmp_tree": tmp_tree()})

doc = {"files": FILES, "manifest": MANIFEST, "viewer_zip": base64.b64encode(VIEWER_ZIP).decode(),
       "web": {u: [s, c, base64.b64encode(b).decode()] for u, (s, c, b) in WEB.items()}, "steps": steps}
with open("fixtures/manager_serve.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
for s in steps:
    print(s["name"], s["result"], len(s["tmp_tree"]), s["requested"])
