"""Export website-mode path mapping parity data from the real WebsiteDownloader.
Run from the repository root."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old"))
from cyoa_downloader_app.download.website import WebsiteDownloader  # noqa: E402
from cyoa_downloader_app.core.paths import _safe_join  # noqa: E402

START = "https://host.example/cyoa/game/"
out_dir = tempfile.mkdtemp(prefix="wsfix_")
d = WebsiteDownloader.__new__(WebsiteDownloader)
d.output_folder = out_dir
d.start_url = START
d.start_html_local = _safe_join(out_dir, "index.html")
d._collision_log = []
rel = lambda p: os.path.relpath(p, out_dir).replace("\\", "/")

kind_cases = [
    ("https://a/x.css", "", ""), ("https://a/x", "text/css; charset=utf-8", ""), ("https://a/x.mjs", "", ""),
    ("https://a/x", "application/javascript", ""), ("https://a/f.woff2", "", ""), ("https://a/x", "font/woff", ""),
    ("https://a/i.SVG", "", ""), ("https://a/x", "image/webp", ""), ("https://a/s.ogg", "", ""),
    ("https://a/v", "video/mp4", ""), ("https://a/project.json", "", ""), ("https://a/d.zip", "", ""),
    ("https://a/x", "application/json", ""), ("https://a/p.htm", "", ""), ("https://a/x", "application/xhtml+xml; q=1", ""),
    ("https://a/x", "", ""), ("https://a/x.css", "", "images"), ("https://a/x.txt", "", ""),
]
norm_cases = [
    ("img/a.png", START), ("/img/a.png", START), ("//cdn.example/x.js", START), ("//cdn.example/x.js", "http://h/"),
    ("https:/cdn.example/y.css", START), ("data:image/png;base64,AA", START), ("javascript:void(0)", START),
    ("#top", START), ("mailto:a@b", START), ("ftp://x/y", START), (" 'q.png' ", START), ("", START),
    ("/_next/image?url=https%3A%2F%2Fimg.example%2Fp.png&w=64", START), ("/_next/image?url=%2Flocal.png&w=64", START),
    ("../up.png", START), ("a b.png?v=1#frag", START), ("HTTPS://UP.EXAMPLE/A.PNG", START), ("blob:xyz", START),
    ("x.png", None), ("chrome://x", START), ("https://h/%E2%9C%93.png", START),
]
alloc_cases = [  # sequential: collisions depend on order
    (START, "text/html", ""), (START + "index.html", "", ""), ("https://host.example/cyoa/game/project.json", "", ""),
    ("https://host.example/cyoa/game/js/app.js", "", ""), ("https://host.example/cyoa/game/js/app.js?v=2", "", ""),
    ("https://host.example/cyoa/game/img/Logo.png", "", ""), ("https://host.example/cyoa/game/img/logo.png", "", ""),
    ("https://host.example/shared/x.css", "", ""), ("https://host.example/cyoa/game/sub/", "text/html", ""),
    ("https://host.example/cyoa/game/api/style", "text/css", ""), ("https://host.example/cyoa/game/proxy.html", "application/javascript", ""),
    ("https://host.example/cyoa/game/img/noext", "image/jpeg", ""), ("https://host.example/cyoa/game/img/noext2", "image/png", ""),
    ("https://host.example/cyoa/game/a%20b/c%27d.png", "", ""), ("https://cdn.other/lib/app.js", "", ""),
    ("https://cdn2.other/lib/app.js", "", ""), ("https://cdn.other/fonts/F.woff2?x=1", "", ""), ("https://cdn.other/", "", ""),
    ("https://cdn.other/data", "application/json", ""), ("https://cdn.other/page", "text/html", ""),
    ("https://cdn.other/" + "n" * 200 + ".png", "", ""), ("https://cdn.other/CON.png", "", ""),
    ("https://cdn.other/run.php", "application/javascript", ""), ("https://cdn.other/s.php", "text/css", ""),
    ("https://cdn.other/m.mp3", "", ""), ("https://cdn.other/weird", "", ""), ("https://host.example/cyoa/game/img/Logo.png", "", ""),
]
alloc = []
for url, ct, pk in alloc_cases:
    try:
        alloc.append([url, ct, pk, rel(d._allocate_local_path(url, ct, pk))])
    except Exception as e:  # record Python errors as parity data too
        alloc.append([url, ct, pk, "ERROR:" + type(e).__name__])
ref_cases = [
    ("index.html", "images/a.png", ""), ("js/app.js", "img/x.png", ""), ("a/b/c.css", "fonts/f.woff2", "https://x/f.woff2#iefix"),
    ("_next/static/chunks/main.js", "images/p.png", ""), ("js/app.abcd1234.js", "img/a b.png", ""),
    ("js/chunk-vendors.12345678.js", "img/@x.png", ""), ("css/site.css", "css/site.css", ""), ("x/y.html", "z@1/a.png", "u#"),
]
refs = [[f, t, s, d._local_asset_reference(os.path.join(out_dir, f), os.path.join(out_dir, t), s)] for f, t, s in ref_cases]
out = {
    "start": START,
    "kind": [[u, c, p, d._kind_from(u, c, p)] for u, c, p in kind_cases],
    "normalize": [[u, r, d._normalize_remote_url(u, r)] for u, r in norm_cases],
    "allocate": alloc,
    "reference": refs,
}
(Path(__file__).parent / "website" / "paths.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print({k: len(v) for k, v in out.items() if isinstance(v, list)})
