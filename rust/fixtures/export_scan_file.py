"""Differential corpus for `asset_scan._scan_file_for_assets` (deep scan core).

Run from the repo root: python rust/fixtures/export_scan_file.py
Writes rust/fixtures/scan_file.json: [{text, file_url, base_url, ext, out}].
Hand-written snippets cover every branch; seeded random concatenations of
those snippets (with separators) exercise interactions. The large-JSON
structural path (>= 2 MiB) is covered by a few generated documents.
"""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.download.asset_scan import _scan_file_for_assets  # noqa: E402

SNIPPETS = [
    # string literals / quotes
    '"img/a.png"', "'img/b.webp?v=2'", "`snd/c.mp3`", '"./rel/d.png"', '"../up/e.jpg"', '"/root/f.gif"',
    '"https://cdn.test/g.png"', '"//cdn.test/h.png"', '"data:image/png;base64,AAA.png"', '"#frag.png"',
    '"a b c.png"', '".png"', '".json"', '"x.json"', '"assets/x.json"', '"style.css"', '"app.wasm"',
    '"font.woff2"', '"m.mp4?x=1"', '"T.PNG"', '"img\\/esc.png"', '"https:\\/\\/cdn.test\\/esc2.png"',
    # js-specific contexts
    'a.download = "canvas.png"', 'x={"../../assets/k.webp": h}', 'new URL("mod.webp", import.meta.url)',
    'new URL(`tpl.webp`, import.meta.url)', "xhr.open('GET', 'data/story.json')", 'item.img = "card.jpg"',
    '"images/chapter/card.jpg"', 'opts.from ? relative(opts.from) : "to.css"', '"${x}.png"', '"fn(a).png"',
    '"import.meta/x.png"', '"application/json,.json"', '"image/png,x.png"',
    # css
    "url(bg.png)", 'url("q.png")', "url('../r.png')", "url(//cdn.test/s.png)", "url(/abs.png)",
    'image-set("i1.png" 1x, "i2.png" 2x)', "new url(not.png)",
    # imports
    'import x from "./mod.js"', 'import "./side.mjs"', 'import("./lazy-abc.js")', 'require("./data.json")',
    'import("./pic.png")', 'from"/abs/mod.js"',
    # vite map deps
    '__vite__mapDeps([0],m=__vite__mapDeps,d=(m.f||(m.f=["assets/a-1.js","b-2.js","./c-3.js"])))',
    # browserify ids
    '},{"./maps/entities.json":22}]',
    # html attrs
    '<img srcset="s1.png 1x, s2.png 2x">', '<div data-src="ds.png" data-bg="bg2.png">', '<video poster="pv.jpg">',
    '<source type="a" src="snd.ogg">', '<link rel="preload" href="pre.woff2" as="font">',
    '<image xlink:href="svgimg.png"/>', '<image href="svg2.png"/>',
    # explicit base
    "var base = new URL('../', document.currentScript.src); load(base + 'css/app.css');",
    # dynamic paths
    "const imageSrc='image/'; imagesToLoad=['card/x.webp','card/y.webp']; img.src = imageSrc + imagesToLoad[i];",
    # document.write loader
    "document.write('<script src=\"data/' + (lang == 'en' ? 'en' : 'id') + '.js?v=3\"></script>');",
    # precache
    'self.__precacheManifest = [{"url": "pc/a.png", "revision": "1"}, {"url": "pc/b.js"}];',
    # tiled overlay
    'window.CYOA = {"sections": [], "options": {}, "meta": {"pages": 2, "pageSize": [100, 2500], "tileHeight": 1000, '
    '"currencies": [{"key": "gold"}], "pathways": [{"key": "p1"}, {"key": "bad key"}]}, '
    '"pages": {"1": {}, "2": {"anim": {"src": "pages/p02/a.webp", "still": "x/s.webp"}}}};',
    # misc unicode / long
    '"ümlaut/ä.png"', '"' + "a/" * 150 + 'long.png"',
]

FILE_CONTEXTS = [
    ("https://site.test/game/assets/app.js", "https://site.test/game/", ".js"),
    ("https://site.test/game/css/main.css", "https://site.test/game/", ".css"),
    ("https://site.test/game/index.html", "https://site.test/game/", ".html"),
    ("https://site.test/game/data/project.json", "https://site.test/game/", ".json"),
    ("https://site.test/assets/vite.js", "https://site.test/assets/", ".mjs"),
    ("https://site.test/img/pic.svg", "https://site.test/", ".svg"),
]

JSON_DOCS = [
    json.dumps({"rows": [{"image": "img/a.png", "html": '<img src="r.png"> url(u.png)',
                          "label": "Luna https:/cdn.test/l.gif", "n": 1},
                         ["x.css", "data:image/png;base64,AA", "#a.png", "no-ext"]]}),
    json.dumps(["a.png", {"k": "https://cdn.test/z.webp"}]),
]

cases = []
for snippet in SNIPPETS:
    for file_url, base_url, ext in FILE_CONTEXTS:
        cases.append((snippet, file_url, base_url, ext))
for doc in JSON_DOCS:
    cases.append((doc, "https://site.test/game/data/project.json", "https://site.test/game/", ".json"))

rng = random.Random(20261118)
separators = [" ", ";", "\n", ",", "+", " = ", "(", ")", "\t", "é"]
for _ in range(1500):
    parts = rng.sample(SNIPPETS, rng.randint(2, 5))
    text = "".join(p + rng.choice(separators) for p in parts)
    cases.append((text, *rng.choice(FILE_CONTEXTS)))

# Large-JSON structural path (>= 2 MiB).
blob = "A" * (2 * 1024 * 1024)
cases.append((json.dumps({"img": "big/a.png", "pad": blob, "sourceTooltip": '<img src="tt.png">',
                          "list": ["//cdn.test/x.png", "c.css"]}),
              "https://site.test/game/data/project.json", "https://site.test/game/", ".json"))
cases.append((json.dumps({"pad": blob}), "https://site.test/game/p.json", "https://site.test/game/", ".json"))
cases.append(("{bad json " + blob + ' "x.png"', "https://site.test/game/p.json", "https://site.test/game/", ".json"))

out = []
for text, file_url, base_url, ext in cases:
    result = sorted(_scan_file_for_assets(text, file_url, base_url, ext))
    big = len(text) >= 1024 * 1024
    out.append({
        # Huge inputs are rebuilt by the test from a recipe to keep the file small.
        "text": None if big else text,
        "recipe": text.replace(blob, "<BLOB>") if big else None,
        "file_url": file_url, "base_url": base_url, "ext": ext, "out": result,
    })

target = ROOT / "rust" / "fixtures" / "scan_file.json"
target.write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", target, len(out), "cases;", sum(1 for o in out if o["out"]), "non-empty")
