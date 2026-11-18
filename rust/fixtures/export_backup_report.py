"""Export Python `format_backup_report_text` outputs (website backup_report.txt parity).

Run from the repo root: python rust/fixtures/export_backup_report.py
Writes rust/fixtures/backup_report.json. The timestamp is pinned so the
outputs are deterministic.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cyoa_downloader_app.diagnostics.reports as reports  # noqa: E402


class _Pinned(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 2, 3, 4, 5)


reports.datetime = _Pinned

CASES = [
    {"start_url": "https://h.test/game/"},
    {
        "start_url": "https://h.test/game/",
        "project_url": "https://h.test/game/data.json",
        "project_root": "project.json",
        "project_aliases": ["data.json", "b.json", "data.json"],
        "downloaded": [
            {"url": "https://h.test/game/css/a.css", "local": "css/a.css", "kind": "css"},
            {"url": "https://h.test/game/img/é.png", "local": "images/é.png", "kind": "images"},
        ],
        "failed": [
            {"url": "https://h.test/game/x.js", "error": "HTTP 404"},
            {"url": "https://h.test/game/y.woff2", "error": ""},
        ],
        "downloaded_groups": {"images": ["images/é.png", "images/b.png", "images/b.png"], "css": ["css/a.css"]},
        "failed_groups": {"js": ["https://h.test/game/x.js"], "fonts": ["https://h.test/game/y.woff2"]},
        "notes": ["Engine mode: standard website", "Project payload written to project.json root."],
    },
    {
        "start_url": "https://h.test/",
        "notes": ["Engine mode: pure website", "Project discovery was intentionally skipped."],
        "failed": [{"url": "https://h.test/z.png"}],
    },
]

out = [{"args": c, "text": reports.format_backup_report_text(**c)} for c in CASES]
path = ROOT / "rust" / "fixtures" / "backup_report.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", path)
