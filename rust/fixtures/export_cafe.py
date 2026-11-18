"""Recorded-transcript fixture for project/cyoa_cafe.py CYOACafeResolver.

Run from the repo root: python rust/fixtures/export_cafe.py
Writes rust/fixtures/cafe.json. Each scenario: canned responses per URL
(status, content type, text; anything else is a 404), the input URL(s), and
the Python result per input: the exact ordered list of fetched URLs and the
resolved URL or the error message. Global resolver caches are cleared before
every scenario (the "twice" scenario resolves the same URL two times to pin
the TTL-cache behaviour within one process).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cyoa_downloader_app.runtime.surface  # noqa: E402,F401  (app import order)
from cyoa_downloader_app.project import cyoa_cafe as cc  # noqa: E402

VIEWER = ('<html><head><title>ICC</title></head><body><div id="app"></div>'
          '<script src="js/app.js"></script></body></html>')
PLAIN = "<html><body><p>Just a blog post</p></body></html>"
API = "https://cyoa.cafe/api/collections/games/records"


def rec(**fields):
    return {"status": 200, "ct": "application/json", "text": json.dumps(fields)}


def html(text, status=200):
    return {"status": status, "ct": "text/html", "text": text}


def listing(*items):
    return {"status": 200, "ct": "application/json",
            "text": json.dumps({"page": 1, "perPage": 10, "totalItems": len(items), "items": list(items)})}


SCENARIOS = {
    "metadata_ok": ({
        f"{API}/abc123": rec(id="abc123", slug="story", iframe_url="https://creator.cyoa.cafe/story/", cyoa_pages=[]),
        "https://creator.cyoa.cafe/story/": html(VIEWER),
    }, ["https://cyoa.cafe/game/abc123"]),
    "metadata_all_404": ({}, ["https://cyoa.cafe/game/zzz999/"]),
    "creator_direct": ({
        "https://creator.cyoa.cafe/story/": html(VIEWER),
    }, ["https://creator.cyoa.cafe/story/"]),
    "creator_game_route": ({
        "https://creator.cyoa.cafe/story/": html(PLAIN),
        "https://creator.cyoa.cafe/story/game/": html(VIEWER),
    }, ["https://creator.cyoa.cafe/story"]),
    "slug_lookup": ({
        f"{API}?filter=slug%3D%27mygame%27&perPage=10": listing(
            {"id": "q1", "slug": "mygame", "iframe_url": "https://other.cyoa.cafe/mygame/"}),
        "https://other.cyoa.cafe/mygame/": html(VIEWER),
    }, ["https://cyoa.cafe/game/mygame"]),
    "record_points_offsite": ({
        f"{API}/off1": rec(id="off1", iframe_url="http://127.0.0.1:9/evil/"),
        "https://cyoa.cafe/game/off1": html('<iframe src="https://good.cyoa.cafe/x/"></iframe>'),
        "https://good.cyoa.cafe/x/": html(VIEWER),
    }, ["https://cyoa.cafe/game/off1"]),
    "html_iframe": ({
        "https://cyoa.cafe/game/page1": html('<html><body><iframe src="https://ifr.cyoa.cafe/p/"></iframe></body></html>'),
        "https://ifr.cyoa.cafe/p/": html(VIEWER),
    }, ["https://cyoa.cafe/game/page1"]),
    "not_cafe": ({}, ["https://example.com/story/?a=1#x"]),
    "twice": ({
        f"{API}/tw1": rec(id="tw1", iframe_url="https://tw.cyoa.cafe/s/"),
        "https://tw.cyoa.cafe/s/": html(VIEWER),
    }, ["https://cyoa.cafe/game/tw1", "https://cyoa.cafe/game/tw1?x=1#y"]),
    "static_pages_record": ({
        f"{API}/st1": rec(id="st1", collectionId="c1", cyoa_pages=["p1.png", "p2.png"], iframe_url=""),
    }, ["https://cyoa.cafe/game/st1"]),
}


class R:
    def __init__(self, spec):
        self.status_code = spec["status"]
        self.text = spec["text"]
        self.headers = {"Content-Type": spec["ct"]}
        self.content = spec["text"].encode("utf-8")
        self.encoding = "utf-8"
        self.url = ""

    def json(self):
        return json.loads(self.text)

    def close(self):
        pass

    def __bool__(self):
        return self.status_code < 400


MISSING = {"status": 404, "ct": "text/html", "text": "not found"}

out = []
for name, (site, inputs) in SCENARIOS.items():
    with cc._CYOA_CAFE_CACHE_LOCK:
        cc._CYOA_CAFE_CACHE.clear()
    for attr in dir(cc):
        obj = getattr(cc, attr)
        if attr.startswith("_CYOA_CAFE_RECORD") and isinstance(obj, dict):
            obj.clear()
    results = []
    for url in inputs:
        log = []

        def fetch(u, timeout=15, _log=log, _site=site):
            _log.append(u)
            return R(_site.get(u, MISSING))

        try:
            value = {"ok": cc.CYOACafeResolver(fetcher=fetch).resolve(url)}
        except cc.CYOACafeResolutionError as exc:
            value = {"error": str(exc)}
        results.append({"url": url, "requests": list(log), **value})
    out.append({"name": name, "site": site, "results": results})

target = ROOT / "rust" / "fixtures" / "cafe.json"
target.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("wrote", target)
for s in out:
    for r in s["results"]:
        print(s["name"], len(r["requests"]), r.get("ok") or ("ERR: " + r["error"][:90]))
