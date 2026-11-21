"""Export Python offline_viewers/injector.py `_apply_offline_viewer` output trees
to fixtures/viewer_inject.json. Run from the repo root:
    python fixtures/make_viewer_inject.py
Uses a temporary HOME; network is replaced with a fake fetcher (FAKE_WEB).
Each case records the returned path (relative to the output dir) and every
file below the output dir (utf-8 text, or base64 for non-UTF-8 bytes).
"""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

home = tempfile.mkdtemp(prefix="cyoa_inject_fixture_")
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
from cyoa_downloader_app.integrations.offline_viewers import injector as inj  # noqa: E402
from cyoa_downloader_app.integrations.offline_viewers import registry as reg  # noqa: E402
import cyoa_downloader_app.network.fetch as netfetch  # noqa: E402

VIEWERS_DIR = reg._VIEWERS_DIR
assert VIEWERS_DIR.startswith(home)
os.makedirs(VIEWERS_DIR, exist_ok=True)

# Fake network: url -> (status, content-type, bytes)
FAKE_WEB = {
    "https://cdn.example.com/lib.js": (200, "application/javascript", b'console.log("lib");var i="img/a.png";'),
    "https://cdn.example.com/style.css": (200, "text/css", b'@import "more.css";body{background:url(bg.png)}'),
    "https://cdn.example.com/more.css": (200, "text/css", b"p{color:red}"),
    "https://cdn.example.com/bg.png": (200, "image/png", b"\x89PNG\r\n\x1a\nBG"),
    "https://cdn.example.com/noext": (200, "application/javascript", b"var n=1;"),
    "https://cdn.example.com/q.js?v=3": (200, "application/javascript", b"var q=3;"),
    "https://site.example.com/rel/pic.webp": (200, "image/webp", b"RIFFxxxxWEBP"),
    "https://cdn.example.com/missing.js": (404, "text/html", b"nope"),
    "https://cdn.example.com/empty.css": (200, "text/css", b""),
    "https://fonts.googleapis.com/css2?family=Open+Sans&family=Roboto+Mono&display=swap": (
        200, "text/css", b"@font-face{src:url(https://fonts.gstatic.com/s/os.woff2)}"),
    "https://fonts.gstatic.com/s/os.woff2": (200, "font/woff2", b"wOF2data"),
    "https://cdn.example.com/f.css": (200, "text/css", b"x{}"),
}


class FakeResp:
    def __init__(self, status, ctype, content):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.content = content

    def close(self):
        pass


requested = []


def fake_fetch(url, timeout=20, extra_headers=None, **_kw):
    requested.append(url)
    if url not in FAKE_WEB:
        return None
    return FakeResp(*FAKE_WEB[url])


netfetch.fetch_response = fake_fetch


def zbytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def put_viewer(name, files):
    with open(os.path.join(VIEWERS_DIR, name), "wb") as f:
        f.write(zbytes(files))


REMIX_INDEX = (
    "<!DOCTYPE html><html><head><title>{{ICC_SITE_TITLE}}</title>{{ICC_FAVICON_TAG}}</head>"
    "<body><div id=\"projectSize\">{{ICC_PROJECT_SIZE}}</div>{{ICC_PROJECT_DATA_SCRIPT}}"
    "<script src=\"app.offline.js\"></script></body></html>"
)
PLUS_INDEX = (
    '<!DOCTYPE html>\n<html lang="en">\n<head prefix="og: x">\n<meta charset="utf-8">\n'
    '<title>Old</title>\n<link rel="icon" href="old.ico">\n<link rel="stylesheet" href="css/app.css">\n'
    "</head>\n<body>\n<div id=\"loadingText\">Loading</div>\n<div class=\"x\" id='projectSize'> 0 </div>\n"
    '<div id="app"></div>\n<script src="js/app.js"></script>\n</body>\n</html>\n'
)
put_viewer("remix.zip", {"ICC Remix/index.html": REMIX_INDEX, "ICC Remix/app.offline.js": "var a=1;",
                         "ICC Remix/sub/": ""})
put_viewer("plus.zip", {
    "index.html": PLUS_INDEX,
    "css/app.css": "body{background:url(../img/bg.png)} .f{background:url('https://cdn.example.com/bg.png')}",
    "img/bg.png": "png",
    "js/app.js": 'var s=1;/*! Delete and replace this part with your project if you\'re pasting it in */ '
                 '{a:{b:"}{"},c:[1,{d:2}]},rest();',
    "js/vendor.js": 'var t="x.png";fetch("https://cdn.example.com/lib.js");',
})
put_viewer("marker_nobrace.zip", {
    "index.html": "<html><body>x</body></html>",
    "app.js": "// Delete and replace this part\nvar nothing;fetch('./project.json').then(r=>r)",
})
put_viewer("fetch.zip", {
    "dist/index.html": "<html><head></head><body><p>v</p></body></html>",
    "dist/app.js": 'a=fetch("project.json");b=fetch(\'project.json\');c=fetch("./project.json");',
    "dist/m.mjs": "x=fetch('./project.json')",
    "dist/other.js": "nothing here",
})
put_viewer("nofetch.zip", {
    "index.html": "<HTML><HEAD data-x=1><META charset=utf-8></HEAD><BODY>z</BODY></HTML>",
    "app.js": "load(`project.json`)",
})
put_viewer("nohead.zip", {"index.html": "plain text viewer", "app.js": "xhr()"})
put_viewer("htmlonly.zip", {"index.html": "<p>only</p></html>", "a.js": ""})
put_viewer("outer.zip", {"pkg/inner.zip": zbytes({"index.html": "<html><head></head><body>in</body></html>",
                                                  "app.js": 'fetch("project.json")'}),
                         "readme.txt": "r"})
put_viewer("crlf.zip", {"index.html": "<html>\r\n<head>\r\n</head>\r\n<body>\rx\r\n</body></html>\r\n",
                        "app.js": "a=1;\r\nb=fetch('project.json');\r\n", "keep.bin": b"\r\n\x00\xff"})
put_viewer("noentry.zip", {"other.html": "x"})
put_viewer("evil.zip", {"../escape.txt": "bad", "index.html": "<html></html>"})
put_viewer("localize.zip", {
    "index.html": (
        '<!DOCTYPE html><html><head>'
        '<script src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon="{}"></script>'
        '<script>/* /cdn-cgi/ challenge */</script>'
        '<script src="https://cdn.example.com/lib.js" integrity="sha-x" crossorigin="anonymous"></script>'
        '<script src="https://cdn.example.com/noext"></script>'
        '<script src="https://cdn.example.com/q.js?v=3"></script>'
        '<script src="https://cdn.example.com/missing.js"></script>'
        '<script src="http://127.0.0.1/internal.js"></script>'
        '<link rel="stylesheet" href="https://cdn.example.com/style.css">'
        '<link rel="stylesheet" href="https://cdn.example.com/empty.css">'
        '<link rel="stylesheet" href="local.css">'
        '<link rel="icon" href="rel/pic.webp">'
        '<style>.a{background:url("https://cdn.example.com/bg.png")} .b{background:url(data:x)}</style>'
        '</head><body><img src="rel/pic.webp"><img src="#x"><img src="data:image/png;base64,AA">'
        '<video src="mailto:x"></video><div style="background:url(https://cdn.example.com/bg.png)">d</div>'
        '<script src="local.js"></script></body></html>'),
    "local.css": "h1{background:url(https://cdn.example.com/bg.png)} h2{background:url(local.png)}",
    "local.png": "lp",
    "local.js": 'var u="https://cdn.example.com/lib.js",r="rel/notdl.png";',
})

PROJECT_REMIX = {"app": {"title": "A & <B> \"q\"", "rows": [{"t": "</script><b>x</b>"}], "n": 1.5,
                         "big": 12345678901234567890, "u": "é😀"}}
PROJECT_PLUS = {
    "version": "2.1", "rows": [], "pointTypes": [], "styling": {},
    "viewerConfig": {"title": "T<1>", "favicon": "fav.png", "loadingText": "Wait 'a' \\ b\nc",
                     "loadingBgImage": "./images/bg.jpg"},
    "googleFonts": ["Open Sans", "Roboto Mono", "Open Sans", ""],
    "customFonts": ["https://cdn.example.com/f.css", "</x>"],
}
PROJECT_PLAIN = {"rows": [{"id": "r1", "s": "{brace}"}], "title": "", "name": "Named"}

cases = []


def run(case_id, meta, project, *, file_name="proj", pre_existing=(), assets=None, source_url="",
        source_html="", cache_dir_files=None, project_text=None):
    out = tempfile.mkdtemp(prefix="out_", dir=home)
    for d in pre_existing:
        os.makedirs(os.path.join(out, d), exist_ok=True)
    asset_dirs = None
    if assets:
        asset_dirs = {}
        for kind, files in assets.items():
            src = os.path.join(home, "assets_" + case_id + "_" + kind)
            for rel, data in files.items():
                p = os.path.join(src, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
            asset_dirs[kind] = src
    cache_dir = ""
    if cache_dir_files:
        cache_dir = os.path.join(home, "cache_" + case_id)
        for rel, data in cache_dir_files.items():
            p = os.path.join(cache_dir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
    text = project_text if project_text is not None else json.dumps(project, ensure_ascii=False, indent=1)
    del requested[:]
    res = inj._apply_offline_viewer(out, text, meta, file_name=file_name, asset_source_dirs=asset_dirs,
                                    source_html=source_html, source_url=source_url,
                                    preserved_asset_cache_dir=cache_dir)
    tree = {}
    for root, _dirs, files in os.walk(out):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, out).replace("\\", "/")
            data = open(p, "rb").read()
            try:
                tree[rel] = {"text": data.decode("utf-8")}
            except UnicodeDecodeError:
                tree[rel] = {"b64": base64.b64encode(data).decode()}
    cases.append({
        "id": case_id, "meta": meta, "project_text": text, "file_name": file_name,
        "pre_existing": list(pre_existing), "assets": {k: {r: base64.b64encode(v).decode() for r, v in f.items()}
                                                       for k, f in (assets or {}).items()},
        "cache_dir_files": {r: base64.b64encode(v).decode() for r, v in (cache_dir_files or {}).items()},
        "source_url": source_url, "source_html": source_html,
        "result": os.path.relpath(res, out).replace("\\", "/") if res else None,
        "requested": list(requested), "tree": tree,
    })
    shutil.rmtree(out, ignore_errors=True)


V = lambda z, **kw: {"name": z, "zip_filename": z, **kw}  # noqa: E731
run("remix", V("remix.zip", viewer_type="custom"), PROJECT_REMIX)
run("plus_marker", V("plus.zip", name="ICC Plus 2 Local"), PROJECT_PLUS, file_name="My CYOA",
    pre_existing=["My CYOA_offline", "My CYOA_offline_1"],
    assets={"images": {"a.png": b"A", "sub/b.jpg": b"\xff\xd8B"}, "audio": {"s.mp3": b"ID3"},
            "fonts": {"f.woff": b"w"}, "bogus": {"x": b"x"}})
run("marker_nobrace", V("marker_nobrace.zip"), PROJECT_PLAIN)
run("fetch_literal", V("fetch.zip", entry_point="dist/index.html"), PROJECT_PLAIN)
run("fetch_entry_walk", V("fetch.zip", entry_point="index.html"), PROJECT_PLAIN)
run("nofetch_interceptor", V("nofetch.zip"), PROJECT_PLAIN)
run("nohead", V("nohead.zip"), {"app": {"name": "N"}})
run("htmlonly", V("htmlonly.zip"), PROJECT_PLAIN)
run("nested", V("outer.zip", inner_archive="pkg/inner.zip"), PROJECT_PLAIN)
run("nested_missing", V("outer.zip", inner_archive="pkg/none.zip"), PROJECT_PLAIN)
run("crlf", V("crlf.zip"), PROJECT_PLAIN)
run("noentry",V("noentry.zip"), PROJECT_PLAIN)
run("evil_member", V("evil.zip"), PROJECT_PLAIN)
run("missing_zip", V("absent.zip"), PROJECT_PLAIN)
run("unsafe_meta", V("../x.zip"), PROJECT_PLAIN)
run("unsafe_inner", V("outer.zip", inner_archive="pkg/inner.rar"), PROJECT_PLAIN)
run("invalid_json", V("fetch.zip", entry_point="dist/index.html"), None, project_text="{bad json")
run("array_json", V("nofetch.zip"), None, project_text="[1,2]")
run("localize", V("localize.zip"), {"app": {"title": "L"}}, source_url="https://site.example.com/page/",
    cache_dir_files={"cdn.example.com/cached.js": b"c"})
run("localize_nosource", V("localize.zip"), {"app": {"title": "L"}}, source_url="")

# Preserved publisher index (source_html): legacy merge, Remix, Plus 2, and none.
LEGACY_SOURCE = (
    '<html><head><title> Pub  Title </title><meta name="author" content="me">'
    '<link rel="stylesheet" href="./theme.css"><link rel="stylesheet" href="css/app.c533aa25.css">'
    '<script src="js/app.c533aa25.js"></script><style>h1{color:blue}</style></head>'
    '<body><div id="app"></div><script src="extra.js"></script></body></html>'
)
put_viewer("legacy.zip", {
    "index.html": "<!DOCTYPE html><html><head><title>Viewer 1.8</title>"
                  '<script src="js/app.c533aa25.js"></script></head><body><div id="app"></div></body></html>',
    "js/app.c533aa25.js": "/*! Delete and replace this part with your project */{x:1};boot()",
})
put_viewer("plus2view.zip", {
    "index.html": "<html><head></head><body><div id=\"app\"></div><script src=\"js/app.js\"></script></body></html>",
    "js/app.js": "/*! Delete and replace this part with your project */{d:1};go()",
    "js/polyfills.js": "p",
})
PLUS2_SOURCE = (
    '<html><head><title>P2</title><script src="core.js"></script>'
    '<link rel="stylesheet" href="css/app.12345678.css"><link rel="stylesheet" href="./mine.css"></head>'
    '<body><div id="app"></div></body></html>'
)
run("src_legacy", V("legacy.zip", viewer_type="icc_original"), PROJECT_PLAIN, source_html=LEGACY_SOURCE)
run("src_legacy_runtime_family", V("legacy.zip", runtime_family="lt_ouroumov"), PROJECT_PLAIN,
    source_html=LEGACY_SOURCE)
run("src_remix", V("remix.zip", viewer_type="icc_remix"), PROJECT_REMIX,
    source_html='<html><head><title>Rx</title><link rel="icon" href="f.png"><script src="./app"></script>'
                "</head><body><p>pub</p></body></html>")
run("src_remix_bad_project", V("remix.zip", viewer_type="icc_remix"), None, project_text="[1]",
    source_html="<html><head></head><body></body></html>")
run("src_plus2", V("plus2view.zip"), {"version": "2.3", "rows": [], "title": "P"}, source_html=PLUS2_SOURCE)
run("src_custom_untouched", V("fetch.zip"), PROJECT_PLAIN, source_html=LEGACY_SOURCE)
run("src_whitespace", V("legacy.zip", viewer_type="icc_original"), PROJECT_PLAIN, source_html="  \n ")

doc = {"viewers": {}, "fake_web": {u: [s, c, base64.b64encode(b).decode()] for u, (s, c, b) in FAKE_WEB.items()},
       "cases": cases, "os_sep_crlf": os.linesep == "\r\n"}
for fn in sorted(os.listdir(VIEWERS_DIR)):
    doc["viewers"][fn] = base64.b64encode(open(os.path.join(VIEWERS_DIR, fn), "rb").read()).decode()
with open("fixtures/viewer_inject.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok", len(cases), "cases;", sum(1 for c in cases if c["result"]), "succeeded")
