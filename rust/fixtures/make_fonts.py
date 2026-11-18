"""Export Python `download/fonts.py` behaviour to fixtures/fonts.json.

Network is replaced by an in-memory site: `sources` (text, for get_source)
and `binaries` (bytes or an HTTP status, for fetch_response).
Run from the repo root: python rust/fixtures/make_fonts.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())
import cyoa_downloader_app.runtime.surface  # noqa: F401  (plugin registration)
from cyoa_downloader_app.download import fonts

CASES = [
    {
        "name": "direct_and_css_urls",
        "project": '{"a":"https://cdn.example/f/Regular.woff2?v=3","css":"url(fonts/b.ttf) url(\'x.png\')","e":"https:\\/\\/cdn.example\\/esc.otf"}',
        "base": "https://site.example/game/",
        "html": "",
        "sources": {},
        "binaries": {
            "https://cdn.example/f/Regular.woff2?v=3": "AAAA",
            "https://cdn.example/esc.otf": "BBBB",
            "https://site.example/game/fonts/b.ttf": "CCCC",
        },
    },
    {
        "name": "icc_plus_google_custom",
        "project": json.dumps({
            "styling": {
                "googleFonts": ["Open Sans", {"family": "Roboto Slab"}, ""],
                "customFonts": ["css/my.css", {"href": "https://other.example/f.css"}],
                "customCSS": "@font-face{src:url('https://cdn.example/c.woff')} @import url(https://fonts.googleapis.com/css?family=Lato);",
            }
        }),
        "base": "https://site.example/game/index.html",
        "html": "",
        "sources": {
            "https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600;700&display=swap": "@font-face{src:url(https://fonts.gstatic.com/s/opensans/v1/os.woff2) format('woff2')}",
            "https://fonts.googleapis.com/css2?family=Roboto+Slab:wght@400;600;700&display=swap": "@font-face{src:url('https://fonts.gstatic.com/s/robotoslab/v2/rs.woff2')}",
            "https://fonts.googleapis.com/css?family=Lato": "@font-face{src:url(https://fonts.gstatic.com/s/lato/v3/lato.ttf)}",
            "https://site.example/game/css/my.css": "@font-face{src:url(../fonts/mine.woff2)}",
            "https://other.example/f.css": "@font-face{src:url(\"o.eot\")}",
        },
        "binaries": {
            "https://fonts.gstatic.com/s/opensans/v1/os.woff2": "0101",
            "https://fonts.gstatic.com/s/robotoslab/v2/rs.woff2": 404,
            "https://fonts.gstatic.com/s/lato/v3/lato.ttf": "0303",
            "https://site.example/game/fonts/mine.woff2": "0404",
            "https://other.example/o.eot": "",
            "https://cdn.example/c.woff": "0606",
        },
    },
    {
        "name": "viewer_html_and_collisions",
        "project": '{"x":"https://a.example/one/Font.woff2","y":"https://b.example/two/Font.woff2","z":"https://a.example/one/Font.woff2?cb=9","w":"https://c.example/Font.woff2"}',
        "base": "https://site.example/",
        "html": '<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Inter"><link rel="Stylesheet preload" href="/local/l.woff"><style>@font-face{src:url("s.ttf")}</style></head></html>',
        "sources": {
            "https://fonts.googleapis.com/css?family=Inter": "@font-face{src:url(https://fonts.gstatic.com/inter.woff2)}",
        },
        "binaries": {
            "https://a.example/one/Font.woff2": "1111",
            "https://b.example/two/Font.woff2": "2222",
            "https://c.example/Font.woff2": "1111",
            "https://site.example/local/l.woff": "3333",
            "https://site.example/s.ttf": "4444",
            "https://fonts.gstatic.com/inter.woff2": "5555",
        },
        "existing": {"Font.woff2": "1111", "s.ttf": "9999"},
    },
    {
        "name": "nothing",
        "project": '{"rows":[]}',
        "base": "https://site.example/",
        "html": "",
        "sources": {},
        "binaries": {},
    },
    {
        "name": "nameless_url",
        "project": '{"a":"https://cdn.example/.woff2","b":"not json url(https://cdn.example/dir/)"}',
        "base": "https://site.example/",
        "html": "",
        "sources": {},
        "binaries": {"https://cdn.example/.woff2": "7777"},
    },
]


class Resp:
    def __init__(self, status, content):
        self.status_code = status
        self.content = content
        self.headers = {}

    def close(self):
        pass


def run(case):
    sources = case["sources"]
    binaries = case["binaries"]
    calls = []

    def get_source(url, *a, **k):
        calls.append(("text", url))
        return sources.get(url)

    def fetch_response(url, *a, **k):
        calls.append(("bytes", url))
        v = binaries.get(url)
        if v is None:
            return None
        if isinstance(v, int):
            return Resp(v, b"")
        return Resp(200, bytes.fromhex(v) if v else b"")

    fonts.get_source = get_source
    fonts.fetch_response = fonts.fetch_response.__class__  # placeholder
    fonts.fetch_response = fetch_response
    found = fonts._find_font_urls(case["project"], case["base"], html_source=case["html"])
    with tempfile.TemporaryDirectory() as tmp:
        if case.get("existing"):
            os.makedirs(os.path.join(tmp, "fonts"))
            for n, h in case["existing"].items():
                with open(os.path.join(tmp, "fonts", n), "wb") as f:
                    f.write(bytes.fromhex(h))
        calls.clear()
        out = fonts._download_fonts_into_folder(case["project"], case["base"], tmp, html_source=case["html"])
        files = {}
        for root, _, names in os.walk(tmp):
            for n in names:
                p = os.path.join(root, n)
                files[os.path.relpath(p, tmp).replace(os.sep, "/")] = open(p, "rb").read().hex()
    return {
        "found": [[u, d] for u, d in found.items()],
        "rewritten": out,
        "files": dict(sorted(files.items())),
    }


identity = [
    "https://A.example/x/f.woff2?v=1&b=2",
    "https://a.example/f.woff2?ts=1&_=2&T=3",
    "http://a.example?x=1#frag",
    "https://a.example/f%20g.woff2?q=a+b&cb",
    "https://a.example/f.woff2#x",
]
is_ref = [
    "https://a.example/f.WOFF2",
    "https://a.example/f.woff2?x=1",
    "data:font/woff2;base64,AAA.woff2",
    "/f.eot",
    "https://a.example/f.svg",
    "https://a.example/f.woff2/",
]
out = {
    "cases": [dict(c, expected=run(c)) for c in CASES],
    "identity": [[u, fonts._font_url_identity(u)] for u in identity],
    "is_font_reference": [[u, fonts._is_font_reference(u)] for u in is_ref],
}
with open("rust/fixtures/fonts.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("ok", len(CASES))
