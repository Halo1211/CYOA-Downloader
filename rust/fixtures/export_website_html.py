"""Export _localize_html parity data (real WebsiteDownloader, stubbed _download_asset)
plus the embedded JS snippets it injects. Run from the repository root."""
import ast, base64, inspect, json, os, sys, tempfile, textwrap, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.download.website import WebsiteDownloader  # noqa: E402
from cyoa_downloader_app.core.paths import _safe_join  # noqa: E402

START = "https://host.example/cyoa/"


def make():
    out = tempfile.mkdtemp(prefix="wshtml_")
    d = WebsiteDownloader.__new__(WebsiteDownloader)
    d.output_folder = out; d.start_url = START; d.base_url = START; d.max_workers = 1
    d.start_html_local = _safe_join(out, "index.html"); d._collision_log = []
    d._downloaded = {}; d._lock = threading.Lock(); d._source_for_local = {}
    d.archive_strategy = "classic"
    calls = []

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


def embedded_strings():
    """The literal JS blobs injected by _localize_html / _patch_local_audio_scripts."""
    import cyoa_downloader_app.download.website as mod
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    found = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name in ("_localize_html", "_patch_local_audio_scripts"):
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    for t in node.targets:
                        label = ast.unparse(t)
                        if label in ("fallback.string", "patch"):
                            found[label] = node.value.value
    return found


inline_doc = "<!DOCTYPE html><html><body><img src=\"img/inline.png\"></body></html>"
b64 = base64.b64encode(inline_doc.encode()).decode()
cases = [
    ("basic", START, "index.html",
     '<!DOCTYPE html><html><head><link rel="stylesheet" href="css/a.css" integrity="sha-x" crossorigin>'
     '<link rel=icon href=/favicon.ico><script src="js/app.js"></script></head>'
     '<body><img src="img/a.png" srcset="img/a.png 1x, img/a@2x.png 2x, data:image/png;base64,AA 3x">'
     '<img src="missing.png"><p style="background:url(img/bg.jpg)">t</p></body></html>'),
    ("base-and-noise", START, "index.html",
     '<html><head><base href="https://cdn.example/root/"><script src="https://www.googletagmanager.com/gtm.js"></script>'
     '<script data-cf-beacon="{}"></script><link rel=preload as=font href="f.woff2"><link rel=manifest href=site.webmanifest>'
     '</head><body><video src=v.mp4 poster=p.jpg></video><source srcset="s.webp"></body></html>'),
    ("lazy-and-misc", START, "index.html",
     '<div data-src="img/lazy.png" data-bg="img/bg2.png"></div><img data-srcset="a.png 1x"><iframe src="frame.html"></iframe>'
     '<iframe src="https://other.example/x.html"></iframe><iframe data-src="same.html"></iframe><object data="o.svg"></object>'
     '<embed src="e.swf"><track src="t.vtt"><svg><use href="#i"></use><image xlink:href="s.png"></image></svg>'
     '<input type=image src="btn.png"><input type=text src="no.png">'),
    ("styles", START, "index.html",
     '<style>@import "theme.css"; body{background:url("img/x.png")}</style><style>  </style>'
     '<a style="color:red">n</a><b style="x:url(i.gif)">y</b>'),
    ("link-kinds", START, "index.html",
     '<link rel="apple-touch-icon" href="t.png"><link rel="modulepreload" href="m.js"><link rel=prefetch href=data.json>'
     '<link rel=preload as=style href=s.css><link rel=preload as=image href=pic><link rel=alternate href=feed.xml>'
     '<link href=/abs/x.css rel=stylesheet><link rel=other href=font.ttf><link rel=other href=lib.mjs>'
     '<link rel="stylesheet" href="https://www.google-analytics.com/a.css"><link href="#x" rel=stylesheet>'),
    ("dice", START, "index.html",
     '<html><body><button aria-label="roll dice AGAIN">r</button></body></html>'),
    ("dice-no-body", START, "index.html", '<button aria-label="Roll dice again">r</button><p>x</p>'),
    ("inline-b64", START, "index.html",
     f'<script>var payload = "{b64}"; document.write(atob(payload));</script>'),
    ("custom-viewer", "https://host.example/cyoa/game", "index.html",
     '<div id="cyoa-container"></div><script>var game_data=1;</script><img src="img/c.png">'),
    ("sub-page", START + "pages/p2.html", "pages/p2.html",
     '<a href="../index.html">home</a><img src="../img/a.png"><img src="pic.png">'),
    ("cloudflare", START, "index.html",
     "<script>window.__CF$cv$params={};var a=document.createElement('iframe');a.src='/cdn-cgi/challenge-platform/x';</script><p>ok</p>"),
    ("runtime-prefetch", START, "index.html",
     '<script>var imgs=["img/r1.png","img/r2.png"];</script><img src="img/r1.png">'),
]
result = []
for name, url, local, html in cases:
    d, out, calls = make()
    d._download_html(url, local_html=os.path.join(out, local), html_text=html)
    written = Path(out, local).read_text(encoding="utf-8")
    result.append({"name": name, "url": url, "local": local, "html": html, "output": written, "calls": calls,
                   "custom_viewer_route": bool(getattr(d, "_custom_viewer_route", False))})
data = {"start": START, "cases": result, "embedded": embedded_strings()}
(Path(__file__).parent / "website" / "html_localize.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(sorted(data["embedded"]), len(result))
