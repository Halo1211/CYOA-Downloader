"""Export fetch_one pre-network decisions from the live Python reference.

Run from repo root: python rust/fixtures/export_fetch_plan.py
resolve: replicates the two lines of fetch_one verbatim (nested fn, not importable).
ssrf: calls the real _ssrf_block_cross_origin with DNS stubbed to a fixed set.
"""
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.getcwd())
from cyoa_downloader_app.integrations import ai_core  # noqa: E402
from cyoa_downloader_app.download import image_pipeline as ip  # noqa: E402

BASES = ["https://example.test/game/", "https://example.test/game/index.html",
         "https://example.test", "http://127.0.0.1:8000/a/b/", "https://h.test/a/b?q=1#f"]
PATHS = ["img/a.png", "/abs/b.jpg", "../up.png", "./x/./y/../z.gif", "//cdn.test/c.png",
         "https:/cdn.test/d.png", "HTTPS:/cdn.test/e.png", "http:/x.test/f", "?v=2", "#frag",
         "https://ok.test/g.png", "HTTP://up.test/h.png", "a;p=1/b.png", "", "..", "../../../../r.png",
         "ünï/ç ode.png", "data/%20sp.png", "mailto:x@y"]
resolve = []
for b in BASES:
    for p in PATHS:
        u = p if p.startswith(("http://", "https://")) else urljoin(b.rstrip("/") + "/", p)
        u = re.sub(r"^(https?):/(?!/)", r"\1://", u, flags=re.IGNORECASE)
        resolve.append({"base": b, "path": p, "url": u})

INTERNAL = {"127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "::1"}
ai_core._host_resolves_internal = lambda h: h.lower() in INTERNAL
ai_core._allow_internal_hosts = False
ASSETS = ["http://127.0.0.1:8000/x.png", "http://127.0.0.1/x.png", "http://127.0.0.1:80/x",
          "https://LOCALHOST/x", "http://169.254.169.254/meta", "https://public.test/x",
          "ftp://127.0.0.1/x", "http://[::1]:8000/x", "http://10.0.0.5:443/x", "https:///nohost"]
SBASES = ["http://127.0.0.1:8000/", "http://localhost/", "https://localhost:443/g", "", "HTTP://127.0.0.1/"]
ssrf = [{"asset": a, "base": b, "block": ai_core._ssrf_block_cross_origin(a, b)}
        for a in ASSETS for b in SBASES]

ATT = ["https://x/a.PNG?x=1", "https://x/gallery/post", "https://x/a.jpeg;p", "https://x/a.mp3",
       "https://x/dir.png/", "https://x/.png", "https://x/a.tiff#f", "https://x/a.webp.html"]
att = []
for u in ATT:
    for img in (True, False):
        ext = os.path.splitext(urlparse(u).path)[1].lower()
        page_like = img and ext not in ip._DEEP_SCAN_IMAGE_EXTENSIONS
        for pre in (True, False):
            att.append({"url": u, "img": img, "pre": pre,
                        "n": 0 if pre else (1 if page_like else 4)})

out = os.path.join("rust", "fixtures", "fetch_plan")
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "cases.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump({"resolve": resolve, "ssrf": ssrf, "attempts": att}, f, ensure_ascii=False, indent=1)
print(len(resolve), len(ssrf), len(att))
