"""Record Python AI analyzer prompts and parsed results (F-043 A3 parity).

`_ai_call` is replaced by a recorder returning canned replies, so no network
is used. Run from the repo root: python rust/fixtures/export_ai_analyzers.py
Writes rust/fixtures/ai_analyzers.json.
"""

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.integrations import ai_calls  # noqa: E402

JS_FILES = [
    {},
    {"assets/app.js": "const a='img/bg.webp';" * 40, "chunk.js": "import('./lazy.js')"},
    {"a.js": "x" * 5000, "b.js": "ÿ" * 3000, "c.js": "short", "d.js": "y" * 900},
]
JS_REPLIES = [
    None,
    "",
    '["/assets/bg.webp", "./chunks/lazy-D1.js", "music/theme.mp3", "https://cdn.test/x.png"]',
    '```json\n["img/a.png", "", 5, "data:x", "javascript:1", "http://127.0.0.1/a.png", "doc.pdf", "api/data", "fonts/f.woff2?v=1", "sub/page.HTML", "img/a b.png"]\n```',
    '{"not": "a list"}',
    "nonsense",
    "```\n[]\n```",
]
VIEWER_REPLIES = [
    None,
    '{"viewer_type": "icc_plus", "data_source": "fetch", "suggestions": "use icc", "extra": 1}',
    '```json\n{"asset_base": "/img/", "chunk_pattern": null, "suggestions": ["a", "b"]}\n```',
    "[1, 2]",
    "garbage",
]
SETTINGS = [{}, {"ai_max_js_chars": "2000", "ai_max_html_chars": 1500}, {"ai_max_js_chars": "abc"},
            {"ai_max_js_chars": 0}]

out = {"js": [], "viewer": [], "detect": []}


def run(fn, replies, settings, **kwargs):
    rows = []
    for reply in replies:
        calls = []

        def fake(**kw):
            calls.append(kw)
            return reply

        with mock.patch.object(ai_calls, "_ai_call", side_effect=fake), \
                mock.patch.object(ai_calls, "_load_settings", return_value=dict(settings)), \
                mock.patch("cyoa_downloader_app.integrations.ai_core._load_settings", return_value=dict(settings)):
            result = fn(**kwargs)
        rows.append({"reply": reply, "calls": calls, "result": result})
    return rows


for st in SETTINGS:
    for js in JS_FILES:
        for mode in ["aggressive_recovery", "auto_fallback"]:
            out["js"].append({"settings": st, "js_files": js, "mode": mode, "rows": run(
                ai_calls._ai_analyze_js_for_assets, JS_REPLIES, st, js_files=js,
                base_url="https://site.test/game/", api_key="k", provider="openai", ai_mode=mode)})
    for html in ["", "<html>" + "h" * 6000 + "</html>"]:
        for js in JS_FILES:
            out["viewer"].append({"settings": st, "html": html, "js_samples": js, "rows": run(
                ai_calls._ai_analyze_viewer_logic, VIEWER_REPLIES, st, html_text=html, js_samples=js,
                url="https://site.test/game/", api_key="k", provider="gemini", ai_mode="diagnostics")})
    for html in ["<script>fetch('data/p.json')</script>", "x" * 9000]:
        out["detect"].append({"settings": st, "html": html, "rows": run(
            ai_calls._ai_detect_project_json,
            [None, "NONE", "data/p.json", "https://a.test/p.json", "http://10.1.1.1/p.json",
             "It is at ./x/project.json"], st,
            url="https://site.test/game/index.html", html_text=html, api_key="k", provider="anthropic",
            ai_mode="auto_fallback")})

path = ROOT / "rust" / "fixtures" / "ai_analyzers.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", path)
