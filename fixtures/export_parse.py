"""Export project/parse.py parity data from live Python. Run from the repository root."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old"))
from cyoa_downloader_app.project import parse as p  # noqa: E402

proj = {"rows": [{"id": "r1", "title": "Row é", "image": "img/a.png", "objects": [{"id": "o1", "image": "b.webp"}]}],
        "backpack": [], "pointTypes": [{"name": "Points", "startingSum": 10}], "styling": {"x": 1.5}}
proj_json = json.dumps(proj, ensure_ascii=False)
texts = [
    "", "{}", "not json", proj_json, json.dumps({"app": proj}), "{rows: [], backpack: [], image: 'x', title: 'T',}",
    "var x = " + proj_json + ";", "window.__INITIAL_STATE__ = " + proj_json + ";",
    "new Vuex.Store({state:{app:" + proj_json + "},getters:{}})",
    "/*! Delete and replace this part */ var app = " + proj_json,
    "function f(){return " + proj_json + "}", "app = reactive({rows: [], backpack: [], 'image': 'x', title: 't'})",
    "{\"rows\": [], // comment }\n \"image\": \"a}b\" }", "x" * 50 + "\"rows\": [{\"image\":\"1\",\"title\":\"t\"}]" + "y" * 50,
    "{'rows': [1,2], 'backpack': [], \"title\": \"Q\"}", "prefix {\"title\": \"no\"} suffix", "日本語 " + proj_json,
    "{\"rows\":[],\"image\":\"x\",\"big\":12345678901234567890,\"f\":1e400}",
]
cases = []
for t in texts:
    cases.append({
        "text": t,
        "looks_like_project_payload": p.looks_like_project_payload(t),
        "extract_json_like_block": p.extract_json_like_block(t),
        "normalize_project_payload_text": p.normalize_project_payload_text(t),
        "extract_project_text_from_payload": p.extract_project_text_from_payload(t),
        "extract_embedded_project_from_js": p.extract_embedded_project_from_js(t),
    })
braces = []
for t, i in [("{a{b}c}", 0), ("x{\"}\"}", 1), ("{/*}*/}", 0), ("{//}\n}", 0), ("{'\\''}", 0), ("{{", 0), ("é{ü}", 1), ("abc", 0)]:
    braces.append({"text": t, "start": i, "ok": p.extract_balanced_brace_block(t, i)})
decodes = []
for raw, pref in [(b"plain", ""), ("é".encode("utf-8"), ""), (b"\xef\xbb\xbfbom", ""), ("hi".encode("utf-16"), ""),
                  (b"\xe9t\xe9", ""), ("日本".encode("shift_jis"), ""), (b"\xe9t\xe9", "utf-8"), ("한국".encode("euc-kr"), "euc-kr")]:
    decodes.append({"raw": base64.b64encode(raw).decode(), "pref": pref, "ok": p.try_decode_bytes(raw, pref)})
out = Path("fixtures/parse")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"texts": cases, "braces": braces, "decodes": decodes}, ensure_ascii=False,
                                           indent=2) + "\n", encoding="utf-8")
print(f"texts {len(cases)} braces {len(braces)} decodes {len(decodes)}")
