"""Export Python modernizer.py preserved-index helpers to fixtures/preserved_index.json.
Run from the repo root: python rust/fixtures/make_preserved_index.py
Pure string functions: build_preserved_index, merge_legacy_index_customizations
and their helpers.
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401,E402
from cyoa_downloader_app.integrations.offline_viewers import modernizer as m  # noqa: E402

TEMPLATES = [
    "<!DOCTYPE html><html><head><title>Viewer</title>"
    '<link rel="stylesheet" href="css/app.c533aa25.css"><script src="js/app.c533aa25.js"></script>'
    "</head><body><div id=\"app\"></div></body></html>",
    "<html><head></head><body>x</body></html>",
    "<html><body>no head</body></html>",
    "plain",
]
SOURCES = [
    "",
    "<html><head><title>  My\n  Great   CYOA </title>"
    '<meta name="description" content="d"><meta charset="utf-8">'
    '<link rel="icon" href="fav.png"><link rel="stylesheet" href="./custom.css">'
    '<link rel="stylesheet" href="css/app.c533aa25.css">'
    '<link rel="stylesheet" href="css/app.publisher-theme.css">'
    "<style>body{color:red}</style>"
    '<script src="js/app.c533aa25.js"></script>'
    '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon="{}"></script>'
    "<script>window.x=1</script></head>"
    '<body><div id="app"></div><script src="js/chunk-vendors.59af3576.js"></script>'
    '<script src="extra.js"></script><script>console.log("b")</script></body></html>',
    '<HEAD><TITLE>Upper</TITLE><LINK REL="shortcut icon" HREF="i.ico"><SCRIPT SRC=\'./a.js\'></SCRIPT></HEAD>'
    "<BODY><SCRIPT SRC='a.js'></SCRIPT></BODY>",
    "<title>t</title><script src=\"core.js\"></script><script src=\"x/core.js\"></script>"
    '<script src="js/app.js?v=1"></script><script src="polyfills.js"></script>',
    '<html><head><script src="/cdn-cgi/scripts/x.js"></script>'
    '<link rel="stylesheet" href=".//style.css"></head><body></body></html>',
]
PLUS2_SOURCES = SOURCES + [
    '<html><head><link href="./_app/immutable/x.css" rel="stylesheet">'
    '<script src="./_app/immutable/start.js"></script>'
    "<script>__sveltekit_1 = {}; import('./_app/immutable/entry.js')</script>"
    '<script src="js/app.B6d7tc9y.js"></script><script src="app"></script>'
    '<link rel="stylesheet" href="css/app.12345678.css"></head>'
    '<body data-sveltekit-preload-data><div id="loading-overlay"></div>'
    '<script src="./js/app.js"></script><div id="app"></div></body></html>',
    '<html><head><script src="__cyoa_offline_project__.js"></script>\n'
    '<script id="__cyoa_offline_patch__">x</script>\n</head><body><main id="app"> </main></body></html>',
    '<html><head><link rel="stylesheet" href="./css/loading.css"></head><body></body></html>',
]
REMIX_SOURCES = SOURCES + [
    '<html><head><title>R</title><link rel="icon" href="f.ico">'
    '<script id="__icc_offline_data__">old</script><script src="./app"></script>'
    '<script src="assets/app.offline.js"></script><script src="js/app.abcdefgh1.js"></script>'
    "<script>window.__ICCPLUS_PLAYABLE_SITE__ = true;</script></head><body></body></html>",
    "<html><head></head><body><script src=\"app.js\"></script></body></html>",
]
PROJECTS = [
    json.dumps({"version": "2.1", "rows": [{"t": "</script>"}], "backpack": [1], "n": 1.5}),
    json.dumps({"rows": [], "title": "é"}, ensure_ascii=False),
    json.dumps({"version": 2, "rows": []}),
]

doc = {"merge": [], "plus2": [], "remix": [], "identity": [], "hashed": [], "strip": []}
for t in TEMPLATES:
    for s in SOURCES:
        doc["merge"].append([t, s, m.merge_legacy_index_customizations(t, s)])
for s in PLUS2_SOURCES:
    doc["plus2"].append([s, PROJECTS[0], m.build_preserved_index(s, m.SiteFamily.ICC_PLUS_2, PROJECTS[0])])
for s in REMIX_SOURCES:
    for p in PROJECTS:
        try:
            out = m.build_preserved_index(s, m.SiteFamily.ICC_REMIX, p)
        except (TypeError, ValueError) as e:
            out = {"error": type(e).__name__}
        doc["remix"].append([s, p, out])
for s in SOURCES + PLUS2_SOURCES[-3:] + REMIX_SOURCES[-2:]:
    doc["identity"].append([s, list(m._html_site_identity(s))])
for name, ext in [("app.c533aa25.js", "js"), ("app.B6d7tc9y.js", "js"), ("app.publisher-theme.css", "css"),
                  ("chunk-vendors.59af3576.js", "js"), ("app.abcdefgh.js", "js"), ("app.1234567.js", "js"),
                  ("APP_12345678.JS", "js"), ("app.js", "js"), ("app-x1y2z3w4.css", "css")]:
    doc["hashed"].append([name, ext, m._is_hashed_runtime_asset(name, ext)])
for s in PLUS2_SOURCES:
    doc["strip"].append([s, m._strip_project_interceptor_markup(s)])
with open("rust/fixtures/preserved_index.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok", {k: len(v) for k, v in doc.items()})
