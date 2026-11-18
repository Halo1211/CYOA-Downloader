"""Fixture for CYOAP Vue pure helpers (_scan_cyoap_assets, _candidate_urls_for_cyoap_asset).

Run from the repo root: python rust/fixtures/export_cyoap.py
Writes rust/fixtures/cyoap.json.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cyoa_downloader_app.runtime.surface  # noqa: E402,F401  (app import order)
from cyoa_downloader_app.core.url_utils import _candidate_urls_for_cyoap_asset  # noqa: E402
from cyoa_downloader_app.project.cyoap_vue import _scan_cyoap_assets  # noqa: E402

BASES = ["https://h.test/cyoa/", "https://h.test/cyoa/index.html", "http://h.test:8080/"]
VALUES = [
    "img/a.png", "/abs/b.png", "//cdn.test/c.png", "cdn.test/d.png", "https://x.test/e.webp",
    "dist/images/f.png", "space name.png", "ünï/çode.jpg", "a%20b.png", "song.mp3", "dist/audio/s.ogg",
    "data:image/png;base64,AA", "", "  pad.png  ", "q.png?v=1#frag", "C:\\x\\y.png", "../up.png",
]
SCAN_DOCS = [
    {"image": "a.png", "imageUrl": "noext", "title": "t", "desc": "see x.png", "bgm": "s.mp3",
     "video": "v.webm", "data": "data:image/png;base64,AA", "nested": [{"img": "n.webp"}, "list.png", 3, None],
     "deep": {"k": {"image": "  spaced.png  "}}, "bad": "http://[bad/x.png", "num": 1.5},
    [{"src": "q.gif"}, {"x": "y.WAV"}],
    {"image": ""},
]

cases = []
for base in BASES:
    for value in VALUES:
        for kind in ("images", "media"):
            cases.append({"base": base, "value": value, "kind": kind,
                          "out": _candidate_urls_for_cyoap_asset(base, value, kind)})
scans = []
for doc in SCAN_DOCS:
    images, media = set(), set()
    _scan_cyoap_assets(doc, images, media)
    scans.append({"doc": doc, "images": sorted(images), "media": sorted(media)})

target = ROOT / "rust" / "fixtures" / "cyoap.json"
target.write_text(json.dumps({"candidates": cases, "scans": scans}, ensure_ascii=False, indent=1) + "\n",
                  encoding="utf-8")
print("wrote", target, len(cases), "candidate cases")
for s in scans:
    print(s["images"], s["media"])
