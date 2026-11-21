"""Export Python offline_viewers.modernizer behaviour to fixtures/modernizer.json.
Run from the repo root:
    python fixtures/make_modernizer.py
The library, viewer collection and HOME are synthetic and live in a temp
dir. Absolute paths in the report are masked as <src>/<dst>. Output trees are
recorded as text (or base64 for non-UTF-8 bytes); mtimes are not compared.
"""
import base64
import io
import json
import os
import sys
import tempfile
import zipfile

home = tempfile.mkdtemp(prefix="cyoa_modernize_")
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
import cyoa_downloader_app.network.fetch as netfetch  # noqa: E402
from cyoa_downloader_app.integrations.offline_viewers import modernizer as mod  # noqa: E402
from cyoa_downloader_app.integrations.offline_viewers import registry as reg  # noqa: E402

requested = []


def no_fetch(url, timeout=20, extra_headers=None, **_):
    requested.append(url)
    return None


netfetch.fetch_response = no_fetch
reg._auto_register_bundled_viewers = lambda: None

MARK = "/*! Delete and replace this part with your project */"


def zbytes(files, root=""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(root + name, data)
    return buf.getvalue()


PLUS2_TEMPLATE = {
    "index.html": "<!DOCTYPE html><html><head><title>T</title></head><body><div id=\"app\"></div>"
                  "<script src=\"./js/app.js\"></script></body></html>",
    "template-metadata.json": "{}",
    "js/app.js": MARK + '{"default":{"a":[1,2]}};boot()',
    "js/polyfills.js": "/*poly*/",
    "css/loading.css": "/*template loading*/",
    "css/smui.css": ".smui{}",
    "css/roboto.css": "@font-face{font-family:R;src:url(../fonts/r.woff2)}",
    "fonts/r.woff2": "FONT-T",
    "favicon.ico": "ICO-T",
}
REMIX_INNER = {
    "index.html": "<html><head><title>R</title></head><body><div id=\"app\"></div></body></html>",
    "app": "/*remix app*/",
    "assets/iccplus_viewer.css": ".remix{}",
    "css/loading.css": "/*remix loading*/",
}
COLLECTION = os.path.join(home, "viewers")
os.makedirs(os.path.join(COLLECTION, "sub"))
with open(os.path.join(COLLECTION, "ICC Plus 2 local viewer v2.0.zip"), "wb") as f:
    f.write(zbytes(PLUS2_TEMPLATE, "viewer/"))
with open(os.path.join(COLLECTION, "sub", "ICC_Remix pack.zip"), "wb") as f:
    f.write(zbytes({"readme.txt": "x", "inner/viewer-template.zip": zbytes(REMIX_INNER)}))
with open(os.path.join(COLLECTION, "old remix.zip"), "wb") as f:
    f.write(zbytes({"index.html": "old"}))
with open(os.path.join(COLLECTION, "Viewer 1.8 [Original].rar"), "wb") as f:
    f.write(b"Rar!")
with open(os.path.join(COLLECTION, "notes.txt"), "w") as f:
    f.write("ignored")
for i, name in enumerate(sorted(os.listdir(COLLECTION))):
    p = os.path.join(COLLECTION, name)
    if os.path.isfile(p):
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))


def icc(version=None, extra=None):
    v = {"rows": [{"id": "r1", "objects": [{"id": "o1"}, {"id": "o2"}]}], "pointTypes": [], "styling": {"a": 1}}
    if version:
        v["version"] = version
    v.update(extra or {})
    return json.dumps(v, ensure_ascii=False)


XHR_LOADER = (
    "const resources = ['a.css','b.js'];\n"
    "let loadedResources = 0;\n"
    "  resources.forEach(function (r) {\n"
    "    var x = new XMLHttpRequest(); x.open('GET', r); loadedResources++;\n"
    "  });\n"
)
LIB = {
    "Plus2 Online": {
        "index.html": "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>My  Plus\n2</title>"
                      "<link rel=\"icon\" href=\"fav.png\">"
                      "<link rel=\"stylesheet\" href=\"css/app.publisher-theme.css\">"
                      "<link rel=\"stylesheet\" href=\"css/app.1a2b3c4d.css\">"
                      "<script src=\"https://cdn.example.com/core.js\"></script>"
                      "</head><body><div id=\"app\"></div><script src=\"js/app.c533aa25.js\"></script>"
                      "<p>custom footer</p></body></html>",
        "project.json": icc("2.1"),
        "css/app.publisher-theme.css": "body{color:red}",
        "css/loading.css": "/*publisher loading*/",
        "fonts/r.woff2": "FONT-PUBLISHER",
        "fav.png": "PNG",
    },
    "Remix Template": {
        "index.html": "<html><head><title>Remix Title</title><link rel=\"shortcut icon\" href=\"f.ico\">"
                      "{{ICC_PROJECT_DATA_SCRIPT}}</head><body>size={{ICC_PROJECT_SIZE}} "
                      "{{ICC_SITE_TITLE}}{{ICC_FAVICON_TAG}}</body></html>",
        "project.json": icc("2.0", {"title": "</script>é"}),
    },
    "Remix Replace": {
        "index.html": "<html><head><script>window.__ICC_REMIX__=1</script>"
                      "<script src=\"app.js\"></script></head><body><div id=\"app\"></div></body></html>",
        "project.json": icc(),
    },
    "Classic": {
        "index.html": "<html><head><script src=\"js/loader.js\"></script></head>"
                      "<body><div id=\"app\"></div><script src=\"js/app.abc12345.js\"></script></body></html>",
        "project.json": icc(),
        "js/app.abc12345.js": "var a=1;" + MARK + "{rows:[],x:{y:'}'}}" + ";"
                              "var r=new XMLHttpRequest();r.open('GET','project.json');"
                              "document.getElementById('lm');document.getElementById(\"indicator\");",
        "js/loader.js": XHR_LOADER,
    },
    "Lt": {
        "index.html": "<html><body><div id='app'></div></body></html>",
        "project.json": icc(),
        "js/app.d3103a3b.js": "// Delete and replace this part\n{rows:[]};",
    },
    "Landing": {
        "index.html": "<html><body><a href='play/'>Play</a></body></html>",
        "project.json": icc(),
        "play/index.html": "<html><body>nested non-site</body></html>",
    },
    "Plain Html": {"index.html": "<html><body>hi</body></html>", "img.png": b"\x89PNG\xff\x00"},
    "No Index": {"project.json": icc(), "readme.txt": "r"},
    "Nested": {
        "index.html": "<html><body>menu</body></html>",
        "game/index.html": "<html><body><div id=\"app\"></div></body></html>",
        "game/project.json": icc(),
        "game/js/app.js": "// Delete and replace this part\n{rows:[1]};",
        "game/js/polyfills.js": "",
    },
    "Broken": {
        "index.html": "<html><body><div id=\"app\"></div></body></html>",
        "project.json": icc(),
        "js/app.11111111.js": MARK + "{rows:[",
    },
    "Remix Done": {
        "index.html": "<html><head><script id=\"__icc_offline_data__\">window.__CYOA_PROJECT__={};"
                      "</script></head><body><div id=\"app\"></div></body></html>",
        "project.json": icc("2.0"),
        "app": "/*app*/",
        "assets/iccplus_viewer.css": "",
    },
    "Unknown": {
        "index.html": "<html><body><div id=\"app\"></div></body></html>",
        "project.json": icc(),
    },
}
LIBDIR = os.path.join(home, "library")
for item, files in LIB.items():
    for rel, data in files.items():
        p = os.path.join(LIBDIR, item, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
os.makedirs(os.path.join(LIBDIR, "zz-file.txt-dir-sibling"), exist_ok=True)
with open(os.path.join(LIBDIR, "loose.txt"), "w") as f:
    f.write("top-level file is skipped")


def tree(root):
    out = {}
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            data = open(p, "rb").read()
            try:
                out[rel] = data.decode("utf-8")
            except UnicodeDecodeError:
                out[rel] = "b64:" + base64.b64encode(data).decode()
    return out


def mask(s):
    # Applied to JSON text: paths appear with escaped backslashes.
    esc = lambda p: json.dumps(p)[1:-1]
    return s.replace(esc(DST), "<dst>").replace(esc(LIBDIR), "<src>").replace(r"\\", "/")


analyze = {}
for item in LIB:
    for dp, _, fns in os.walk(os.path.join(LIBDIR, item)):
        if "index.html" in fns or dp == os.path.join(LIBDIR, item):
            pr = mod.analyze_site(dp)
            rel = os.path.relpath(dp, LIBDIR).replace("\\", "/")
            site = str(pr.site_dir)

            def r(p):
                return None if p is None else os.path.relpath(str(p), site).replace("\\", "/")

            analyze[rel] = {
                "family": pr.family.value, "strategy": pr.strategy, "index": r(pr.index_path),
                "project": r(pr.project_path), "marker": r(pr.marker_script),
                "confidence": pr.confidence, "reasons": list(pr.reasons),
            }

templates = {f.value: [os.path.relpath(str(t.archive_path), COLLECTION).replace("\\", "/"), t.inner_archive]
             for f, t in mod.resolve_viewer_templates(COLLECTION).items()}

DST = os.path.join(home, "out")
progress = []
report = mod.modernize_collection(LIBDIR, DST, viewer_collection=COLLECTION,
                                  progress=lambda a, b, n: progress.append([a, b, n]))
report_text = open(os.path.join(DST, "conversion_report.json"), encoding="utf-8").read()
out_tree = tree(DST)
del out_tree["conversion_report.json"]

# In-place website hook with registered templates (orchestrator.py:744).
os.makedirs(reg._VIEWERS_DIR, exist_ok=True)
with open(os.path.join(reg._VIEWERS_DIR, "localviewer v2.zip"), "wb") as f:
    f.write(zbytes(PLUS2_TEMPLATE))
with open(os.path.join(reg._VIEWERS_DIR, "online v2.zip"), "wb") as f:
    f.write(zbytes({"index.html": "x", "core.js": ""}))
with open(reg._VIEWERS_MANIFEST, "w", encoding="utf-8") as f:
    json.dump({
        "a": {"name": "Plus 2", "zip_filename": "localviewer v2.zip", "viewer_type": "icc_plus"},
        "b": {"name": "Plus 2 web", "zip_filename": "online v2.zip", "viewer_type": "icc_plus2"},
        "c": {"name": "missing", "zip_filename": "gone.zip", "viewer_type": "icc_remix"},
    }, f)
registered = {f.value: [os.path.basename(str(t.archive_path)), t.inner_archive]
              for f, t in mod.resolve_registered_viewer_templates().items()}
SITE = os.path.join(home, "site")
for rel, data in LIB["Plus2 Online"].items():
    p = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(data)
res = mod.modernize_site(SITE, SITE, templates=mod.resolve_registered_viewer_templates(),
                         source_url="https://example.com/cyoa/")
hook = {"family": res.family.value, "strategy": res.strategy, "status": res.status,
        "message": res.message, "changed": list(res.changed_files), "tree": tree(SITE)}

def raw_tree(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            out[rel] = [base64.b64encode(open(p, "rb").read()).decode(), int(os.stat(p).st_mtime)]
    return out


json.dump({
    "inputs": {
        "library": {k: {r: (base64.b64encode(v if isinstance(v, bytes) else v.encode()).decode())
                        for r, v in files.items()} for k, files in LIB.items()},
        "collection": raw_tree(COLLECTION),
        "viewers": raw_tree(reg._VIEWERS_DIR),
    },
    "analyze": analyze,
    "templates": templates,
    "progress": progress,
    "summary": [report.total_items, report.modernized, report.copied, report.failed],
    "report": json.loads(mask(json.dumps(json.loads(report_text), ensure_ascii=False))),
    "tree": out_tree,
    "registered": registered,
    "hook": hook,
    "requested": requested,
}, open("fixtures/modernizer.json", "w", encoding="utf-8", newline="\n"),
    ensure_ascii=False, indent=1, sort_keys=True)
print("ok", report.total_items, report.modernized, report.copied, report.failed, len(out_tree))
