"""Export the orchestrator's integrity append to backup_report.txt.

Run from the repo root: python rust/fixtures/export_integrity_report.py
Writes rust/fixtures/integrity_report.json. The append block is copied
verbatim from download/orchestrator.py (ICC branch); the file is read back in
binary so platform newlines are recorded.
"""

import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

integrity = {
    "missing": ["index.html → a.png", "css" + os.sep + "x.css → b.png"],
    "external": ["index.html → https://c/x.js"],
}
with tempfile.TemporaryDirectory() as tmp:
    report_path = os.path.join(tmp, "backup_report.txt")
    with open(report_path, "w", encoding="utf-8") as head:
        head.write("HEAD\n")
    # verbatim from orchestrator.py
    with open(report_path, "a", encoding="utf-8") as _rf:
        _rf.write("\n" + "=" * 60 + "\n")
        _rf.write("INTEGRITY CHECK — MISSING LOCAL REFS\n")
        _rf.write("=" * 60 + "\n")
        _rf.writelines(f"  {miss}\n" for miss in integrity["missing"])
        if integrity.get("external"):
            _rf.write("\nREACHABLE EXTERNAL DEPENDENCIES\n")
            _rf.writelines(f"  {external}\n" for external in integrity["external"])
    data = Path(report_path).read_bytes().decode("utf-8")

target = ROOT / "rust" / "fixtures" / "integrity_report.json"
target.write_text(
    json.dumps({"integrity": integrity, "bytes": data}, ensure_ascii=False, indent=1) + "\n",
    encoding="utf-8",
)
print("wrote", target)
