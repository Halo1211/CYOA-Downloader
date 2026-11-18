"""Export CSS rewrite parity data from the real WebsiteDownloader._process_css with a
deterministic stubbed _download_asset. Run from the repository root."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.download.website import WebsiteDownloader  # noqa: E402
from cyoa_downloader_app.core.paths import _safe_join  # noqa: E402

START = "https://host.example/cyoa/"

def make():
    out = tempfile.mkdtemp(prefix="wscss_")
    d = WebsiteDownloader.__new__(WebsiteDownloader)
    d.output_folder = out; d.start_url = START; d.base_url = START
    d.start_html_local = _safe_join(out, "index.html"); d._collision_log = []
    d._downloaded = {}; calls = []
    def fake(url, preferred_kind="", referrer_url=None):
        full = d._normalize_remote_url(url, referrer_url)
        calls.append([url, preferred_kind, referrer_url, full])
        if not full or "missing" in full:
            return None
        full = full.split("#", 1)[0]
        if full in d._downloaded:
            return d._downloaded[full]
        local = d._allocate_local_path(full, "", preferred_kind)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        Path(local).write_bytes(b"x")
        d._downloaded[full] = local
        return local
    d._download_asset = fake
    return d, out, calls

cases = [
 ("a", 'body{background:url(img/bg.png)}', "https://host.example/cyoa/css/site.css", "css/site.css", []),
 ("quotes", "a{b:url( 'img/x.png' )} c{d:url(\"//cdn.example/y.webp?v=1#k\")}", "https://host.example/cyoa/css/site.css", "css/site.css", []),
 ("import", '@import "theme.css";\n@import url(https://fonts.example/f.css) screen;\n@import url(\'missing.css\');', "https://host.example/cyoa/css/main.css", "css/main.css", []),
 ("comments", '/* @import is cool; url(nope.png) */ a{x:url(ok.png)} /* "q.png" */', "https://host.example/cyoa/s.css", "s.css", []),
 ("skip", 'a{x:url(data:image/png;base64,AA)} b{y:url(#frag)} c{z:url(missing.png)} d{w:url()}', "https://host.example/cyoa/s.css", "s.css", []),
 ("existing", 'a{x:url(../img/have.png)} b{y:url("img/new.png")}', "https://host.example/cyoa/css/s.css", "css/s.css", ["img/have.png"]),
 ("direct", 'a{content:"fonts/x.woff2"} b{x:"https://cdn.example/a.svg"} c{y:"plain text"} d{z:"\\"esc.png\\""}', "https://host.example/cyoa/s.css", "s.css", []),
 ("embedded", 'a{x:url(img/one.png)} /* see */ b{content:"label https://host.example/cyoa/img/one.png more"}', "https://host.example/cyoa/s.css", "s.css", []),
 ("upper", 'A{B:URL(Img/Up.PNG)} @IMPORT "x.CSS";', "https://host.example/cyoa/s.css", "s.css", []),
 ("multi", 'a{background:url(a.png),url(b.png) , url( "c.png" )}', "https://host.example/cyoa/deep/er/s.css", "deep/er/s.css", []),
 ("unterminated", 'a{x:url(a.png} /* open', "https://host.example/cyoa/s.css", "s.css", []),
 ("cross", 'a{x:url(https:/cdn.example/fix.png)} b{y:url(/root.png)} c{z:url(../../up.gif)}', "https://host.example/cyoa/css/s.css", "css/s.css", []),
]
result = []
for name, css, url, local, pre in cases:
    d, out, calls = make()
    for p in pre:
        f = os.path.join(out, p); os.makedirs(os.path.dirname(f), exist_ok=True); Path(f).write_bytes(b"x")
    got = d._process_css(css, url, os.path.join(out, local))
    rel = lambda v: os.path.relpath(v, out).replace("\\", "/") if isinstance(v, str) and os.path.isabs(v) else v
    result.append({"name": name, "css": css, "css_url": url, "css_local": local, "existing": pre,
                   "output": got, "calls": [[c[0], c[1], c[2]] for c in calls]})
(Path(__file__).parent / "website" / "css.json").write_text(json.dumps({"start": START, "cases": result}, ensure_ascii=False, indent=1), encoding="utf-8")
for r in result: print(r["name"], "|", r["output"])
