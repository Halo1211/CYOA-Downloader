"""Export Python write_project_payload behaviour with deterministic stubs.

Run from the repo root: python fixtures/export_project_payload.py
Writes fixtures/project_payload.json.

`_process_css` is stubbed as "<call#>:" + value.upper() (records the LIFO
traversal order) and `_download_asset` as a fake local path under the output
folder (or None for families containing "Missing"), so the Rust generic
helpers can be compared without network access. Files are read back in binary
(platform newlines included).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "old"))

from cyoa_downloader_app.download.website import WebsiteDownloader  # noqa: E402

PROJECTS = [
    '{"rows":[]}',
    json.dumps({
        "customCss": "a{}",
        "rows": [{"customCSS": "b{}", "x": {"CustomCss": "c{}"}}, {"customcss": "d{}"}],
        "styling": {"customCss": "e{}"},
        "n": 1.5,
        "i": 12345678901234567,
        "u": "caf" + chr(0xE9) + chr(0x2028) + chr(1) + chr(0x7F) + "/" + chr(0x1F600),
    }),
    '{"customCss":"","rows":[]}',
    '{"customCss":5,"rows":[]}',
    '[1,2]',
    'not json',
]
FONTS_PROJECTS = [
    '{"googleFonts":["Roboto","Open Sans","  ",7,"Missing Font"],"rows":[]}',
    '{"googleFonts":[],"rows":[]}',
    '{"googleFonts":"Roboto"}',
]
JS = {
    "js/app.js": 'load("https://fonts.googleapis.com/css2?family=".concat(n,"&display=swap"));\n',
    "js/sub/b.js": "x=`https://fonts.googleapis.com/css2?family=${f}&display=swap`;",
    "js/none.js": "nothing();",
}

css_cases = []
for text in PROJECTS:
    with tempfile.TemporaryDirectory() as tmp:
        d = WebsiteDownloader("https://h.test/game/", tmp)
        calls = []

        def fake_css(value, base, local, _calls=calls):
            _calls.append(value)
            return f"{len(_calls)}:" + value.upper() if value else value

        d._process_css = fake_css
        css_cases.append({"in": text, "out": d.localize_project_custom_css(text), "calls": calls})

font_cases = []
for text in FONTS_PROJECTS:
    with tempfile.TemporaryDirectory() as tmp:
        d = WebsiteDownloader("https://h.test/game/", tmp)
        for rel, body in JS.items():
            p = Path(tmp, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body.encode("utf-8"))
        requested = []

        def fake_asset(url, preferred_kind="", referrer_url=None, _req=requested, _tmp=tmp):
            _req.append([url, preferred_kind, referrer_url])
            if "Missing" in url:
                return None
            return os.path.join(_tmp, "fonts", f"f{len(_req)}.css")

        d._download_asset = fake_asset
        changed = d.localize_project_google_fonts(text)
        css = Path(tmp, "css", "offline-google-fonts.css")
        font_cases.append({
            "in": text,
            "changed": changed,
            "requested": requested,
            "stylesheet": css.read_bytes().decode("utf-8") if css.exists() else None,
            "js": {rel: Path(tmp, rel).read_bytes().decode("utf-8") for rel in JS},
        })

out = {"js": JS, "custom_css": css_cases, "google_fonts": font_cases}
target = ROOT / "rust" / "fixtures" / "project_payload.json"
target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", target)
