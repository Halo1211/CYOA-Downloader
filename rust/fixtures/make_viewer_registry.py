"""Export Python register_offline_viewer / unregister_offline_viewer behaviour
to fixtures/viewer_registry.json. Run from the repo root:
    python rust/fixtures/make_viewer_registry.py
`registered_at` is masked; archives are compared by member names + contents.
"""
import base64
import io
import json
import os
import sys
import tempfile
import zipfile

home = tempfile.mkdtemp(prefix="cyoa_viewer_reg_")
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
from cyoa_downloader_app.integrations.offline_viewers import registry as reg  # noqa: E402

reg._auto_register_bundled_viewers = lambda: None
SRC = os.path.join(home, "src")
os.makedirs(SRC)


def mkzip(name, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    with open(os.path.join(SRC, name), "wb") as f:
        f.write(buf.getvalue())


mkzip("Viewer 1.8 [Original].zip", {"index.html": "<html>", "js/app.c533aa25.js": "x"})
mkzip("New Viewer 1.18.9.zip", {"index.html": "<html>", "js/chunk-vendors.59af3576.js": "x"})
mkzip("ICC Plus 2 v2.zip", {"v/index.html": "a", "v/js/app.js": "b", "v/js/polyfills.js": "c"})
mkzip("online.zip", {"index.html": "a", "core.js": "b"})
mkzip("Remix Editor.zip", {"index.html": "e", "x/a/viewer-template.zip": "z", "viewer-template.zip": "y"})
mkzip("om.zip", {"index.html": "a", "assets/index-abc.js": "b"})
mkzip("plain.zip", {"index.html": "a"})
with open(os.path.join(SRC, "bad.zip"), "wb") as f:
    f.write(b"not a zip")
FOLDER = os.path.join(SRC, "My Folder Viewer")
for rel, data in {"index.html": "f", "js/app.js": "g", "js/polyfills.js": "h", "css/z.css": "i"}.items():
    p = os.path.join(FOLDER, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(data)

CASES = [
    ["Viewer 1.8 [Original].zip", {}],
    ["New Viewer 1.18.9.zip", {"name": "Legacy"}],
    ["ICC Plus 2 v2.zip", {"viewer_type": "icc_plus", "description": "d"}],
    ["online.zip", {"viewer_type": "icc_plus2"}],
    ["Remix Editor.zip", {"viewer_type": "icc_plus"}],
    ["om.zip", {}],
    ["plain.zip", {"entry_point": "play/start.html", "project_json_path": "data/p.json"}],
    ["plain.zip", {"entry_point": "../evil.html"}],
    ["plain.zip", {"project_json_path": "../p.json"}],
    ["bad.zip", {}],
    ["missing.zip", {}],
    ["My Folder Viewer", {"name": "Folder"}],
    ["online.zip", {"name": "Re-registered"}],
]
results = []
for src, kw in CASES:
    results.append(reg.register_offline_viewer(os.path.join(SRC, src), **kw))
unreg = [
    reg.unregister_offline_viewer("om", delete_zip=True),
    reg.unregister_offline_viewer("plain", delete_zip=False),
    reg.unregister_offline_viewer("nope"),
]
manifest = json.load(open(reg._VIEWERS_MANIFEST, encoding="utf-8"))
for v in manifest.values():
    v["registered_at"] = "<ts>"
files = {}
for name in sorted(os.listdir(reg._VIEWERS_DIR)):
    p = os.path.join(reg._VIEWERS_DIR, name)
    if name.endswith(".zip"):
        with zipfile.ZipFile(p) as z:
            files[name] = {n: base64.b64encode(z.read(n)).decode() for n in z.namelist()}
    else:
        files[name] = None
json.dump({"cases": CASES, "results": results, "unregister": unreg, "manifest": manifest,
           "manifest_text_indent2": open(reg._VIEWERS_MANIFEST, encoding="utf-8").read().startswith("{\n  \""),
           "files": files},
          open("rust/fixtures/viewer_registry.json", "w", encoding="utf-8", newline="\n"),
          ensure_ascii=False, indent=1)
print(results, unreg)
