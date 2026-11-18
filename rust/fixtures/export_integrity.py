"""Export Python WebsiteDownloader.validate_integrity results on a fixture site.

Run from the repo root: python rust/fixtures/export_integrity.py
Writes rust/fixtures/integrity.json: the site tree (path -> UTF-8 text) and the
Python result. Labels contain the platform separator (os.path.relpath), so the
fixture records `os.sep` too.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.download.website import WebsiteDownloader  # noqa: E402

TREE = {
    "index.html": """<!doctype html><html><head>
<link rel="stylesheet" href="css/a.css">
<link rel="stylesheet" href="css/missing.css">
<link rel="icon" href="img/favicon.ico">
<link rel="alternate" href="data/feed.json">
<link rel="canonical" href="about">
<script src="js/app.js"></script>
<script src="https://cdn.example/lib.js"></script>
<style>/* url(img/commented.png) */ body{background:url('img/ok.png')} @import "css/b.css";</style>
</head><body>
<img src="img/ok.png?v=1#x"><img src="img/missing.png"><img src="img/OK.PNG">
<img srcset="img/ok.png 1x, img/two.png 2x" src="data:image/png;base64,AAAA">
<div style="background:url(&quot;img/bg.png&quot;)"></div>
<iframe srcdoc="<p>x</p>" src="frame-missing.html"></iframe>
<object data="media/clip.swf"></object>
<svg><use href="img/sprite.svg#icon"></use><image xlink:href="img/svgimg.png"></image></svg>
<a href="sub/page2.html">p2</a><a href="about">about</a><a href="route" data-cyoa-local-route>r</a>
<a href="mailto:x@y">m</a><a href="#top">t</a>
<img src="img/caf%C3%A9.png"><img src="/img/ok.png"><img src="//cdn.example/x.png">
<video poster="img/poster.jpg"></video>
</body></html>""",
    "sub/page2.html": '<img src="../img/ok.png"><img src="/img/ok.png"><img src="nope.png"><a href="../index.html">i</a>',
    "css/a.css": """@import url("b.css");
/* url(img/commented.png) */
.x{background:url(../img/ok.png)}
.y{background:url("/img/abs-missing.png")}
@font-face{font-family:F;src:url(../fonts/f.woff2) format("woff2"),url(../fonts/f.ttf) format("truetype")}
@font-face{font-family:G;src:url(../fonts/g.woff2),url(../fonts/g.ttf)}
""",
    "css/b.css": ".z{background:url(missing2.png)}",
    "css/orphan.css": ".o{background:url(nothing.png)}",
    "js/app.js": """import x from "./mod.js";
import("./lazy.js");
const u = new URL("../img/ok.png", import.meta.url);
el.src = "img/dyn${n}.png";
el.href = 'static/chunks/c.js';
const msg = "see random.js for details";
""",
    "js/mod.js": "export default 1;",
    "static/chunks/c.js": "",
    "img/ok.png": "png",
    "img/two.png": "png",
    "img/café.png": "png",
    "img/sprite.svg": "<svg/>",
    "fonts/f.woff2": "font",
    "__original_site__/old.html": '<img src="gone.png">',
    "data/feed.json": "{}",
}

with tempfile.TemporaryDirectory() as tmp:
    for rel, text in TREE.items():
        p = Path(tmp, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    d = WebsiteDownloader("https://h.test/game/", tmp)
    result = d.validate_integrity()

out = {"sep": os.sep, "tree": TREE, "result": result}
target = ROOT / "rust" / "fixtures" / "integrity.json"
target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", target)
