"""Export JS rewrite parity data from the real WebsiteDownloader._process_js (stubbed
_download_asset, max_workers=1 for deterministic order). Run from the repository root."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.download.website import WebsiteDownloader, _infer_runtime_chunk_paths  # noqa: E402
from cyoa_downloader_app.download.asset_scan import _infer_dynamic_asset_paths  # noqa: E402
from cyoa_downloader_app.core.paths import _safe_join  # noqa: E402

START = "https://host.example/cyoa/"


def make():
    out = tempfile.mkdtemp(prefix="wsjs_")
    d = WebsiteDownloader.__new__(WebsiteDownloader)
    d.output_folder = out; d.start_url = START; d.base_url = START; d.max_workers = 1
    d.start_html_local = _safe_join(out, "index.html"); d._collision_log = []
    d._downloaded = {}; calls = []

    def fake(url, preferred_kind="", referrer_url=None):
        full = d._normalize_remote_url(url, referrer_url)
        calls.append([url, preferred_kind, referrer_url])
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


JS = "https://host.example/cyoa/js/"
cases = [
    ("plain", 'var a="img/a.png"; fetch("data/project.json"); x(\'//cdn.example/l.js\')', JS + "game.js", "js/game.js", []),
    ("dynamic-loader", "basePath = new URL('../', document.currentScript.src); load(basePath + 'css/x.css'); load(basePath + \"img/y.png\")", JS + "core.js", "js/core.js", []),
    ("webpack-public", '__webpack_public_path__ = "/"; a("js/chunk.js"); b("css/c.css"); c("i.png")', JS + "boot.js", "js/boot.js", []),
    ("app-bundle", 'n.p="/";var u="https://cdn.example/big.png";var m=n.p+"js/"+e+"."+{"1":"aaaa1111","2":"bbbb2222"}[e]+".js";', JS + "app.12345678.js", "js/app.12345678.js", []),
    ("chunkmap2", 'x="js/"+({}[e]||e)+"."+{"10":"cafe0000"}[e]+".js"', JS + "runtime.js", "js/runtime.js", []),
    ("preload-array", 'var urls=["a.png","b.png","sub/c.png"];for(i in urls){img.src="images/"+urls[i];}', JS + "pre.js", "js/pre.js", []),
    ("module-relative", 'import x from "./mod.js"; import("../lazy.mjs"); new URL("./w.wasm", import.meta.url); var s="css/site.css";', JS + "entry.mjs", "js/entry.mjs", []),
    ("random-range", 'r = Math.floor(Math.random() * 3) + 1; el.src = "comics/" + r + ".png";', JS + "rnd.js", "js/rnd.js", []),
    ("template", 'const cards=[{slug:"alpha"},{slug:\'beta\'}]; html=`<img src="pics/${c.slug}.webp">`', JS + "tpl.js", "js/tpl.js", []),
    ("dynamic-tokens", 'const imgBase="images/"; const imageList=["x.jpg","y.jpg"]; var other="z.jpg";', JS + "dyn.js", "js/dyn.js", []),
    ("escaped", 'a="\\"esc.png\\""; b="img\\/slash.png"; c="${v}.png"; d="w3.org/x.svg"', JS + "e.js", "js/e.js", []),
    ("vercel", 's.src="https://vercel.live/_next-live/feedback/feedback.js";t="img/t.png"', JS + "v.js", "js/v.js", []),
    ("existing", 'a="../img/have.png"; b="img/new.png"; c="missing/x.png"', JS + "ex.js", "js/ex.js", ["img/have.png"]),
    ("embedded", 'var p="https://host.example/cyoa/img/one.png"; var q="see https://host.example/cyoa/img/one.png now"', JS + "emb.js", "js/emb.js", []),
]
result = []
for name, js, url, local, pre in cases:
    d, out, calls = make()
    for p in pre:
        f = os.path.join(out, p); os.makedirs(os.path.dirname(f), exist_ok=True); Path(f).write_bytes(b"x")
    got = d._process_js(js, url, os.path.join(out, local))
    result.append({"name": name, "js": js, "js_url": url, "js_local": local, "existing": pre,
                   "output": got, "calls": calls})

chunk_inputs = [c[1] for c in cases] + [
    'a.p="/static/";x="js/"+e+"."+{"a":"1","b":"2"}[e]+".js"',
    "x='css/'+e+''+{\"3\":\"ffff\"}[e]+'.css'",
]
dyn_inputs = [c[1] for c in cases] + [
    'setup.WK_IMG_DIR = "images/07_Weakness/"; var meta={img: "boss.png"}',
    'src=&quot;images/06_Anomalies/&#39; + row.img + x; {"img":"ghost.webp"}',
    'let assetDir="assets/"; let assetFiles=["one.png","assets/two.png"];',
]
out = {
    "start": START,
    "cases": result,
    "chunks": [[t, _infer_runtime_chunk_paths(t)] for t in chunk_inputs],
    "dynamic": [[t, {k: sorted(v) for k, v in _infer_dynamic_asset_paths(t).items()}] for t in dyn_inputs],
}
(Path(__file__).parent / "website" / "js.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for r in result:
    print(r["name"], "|", r["output"][:150], "| calls:", len(r["calls"]))
