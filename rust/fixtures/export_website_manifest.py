"""Export Python WebsiteDownloader.write_manifest output (backup_report.txt).

Run from the repo root: python rust/fixtures/export_website_manifest.py
Writes rust/fixtures/website_manifest.json. State is set directly (no network)
and the timestamp is pinned. The file is read back in binary so the platform
newline translation (CRLF on Windows) is recorded too.
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cyoa_downloader_app.diagnostics.reports as reports  # noqa: E402
from cyoa_downloader_app.download.website import WebsiteDownloader  # noqa: E402


class _Pinned(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 2, 3, 4, 5)


reports.datetime = _Pinned

STATE = {
    "start_url": "https://h.test/game/",
    "success": [
        ["https://h.test/game/css/a.css", "css/a.css", "css"],
        ["https://h.test/game/img/é.png", "images/é.png", "images"],
        ["https://h.test/game/css/a.css", "css/a.css", "css"],
        ["https://h.test/game/app.js", "js/app.js", "js"],
    ],
    "failed": [
        ["https://h.test/game/x.js", "HTTP 404"],
        ["https://h.test/game/song.mp3?x=1", "timeout"],
        ["https://h.test/game/.hidden", "HTTP 403"],
        ["https://h.test/game/f.WOFF2", ""],
        ["https://h.test/game/x.js", "HTTP 404"],
    ],
    "aliases": ["data.json"],
    "collisions": [["https://h.test/a/logo.png", "images/logo.png", "images/logo_1.png"]],
}

cases = []
for project_url in ("https://h.test/game/data.json", ""):
    with tempfile.TemporaryDirectory() as tmp:
        d = WebsiteDownloader(STATE["start_url"], tmp)
        d._success_items = [{"url": u, "local": l, "kind": k} for u, l, k in STATE["success"]]
        d._failed_items = [{"url": u, "error": e} for u, e in STATE["failed"]]
        d._project_aliases = list(STATE["aliases"])
        d._collision_log = [
            {"url": u, "original_path": o, "saved_as": s} for u, o, s in STATE["collisions"]
        ]
        path = d.write_manifest(project_url=project_url)
        cases.append({"project_url": project_url, "bytes": Path(path).read_bytes().decode("utf-8")})

out = {"state": STATE, "cases": cases}
target = ROOT / "rust" / "fixtures" / "website_manifest.json"
target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", target)
