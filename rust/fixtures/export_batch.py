"""Export batch-import parity data from the real importer. Run from repo root."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.importers.batch import import_queue_items_from_source, _derive_mode_flags  # noqa: E402
d = Path(__file__).parent / "batch" / "in"
out = {p.name: import_queue_items_from_source(str(p)) for p in sorted(d.iterdir())}
out["__missing__"] = import_queue_items_from_source(str(d / "nope.txt"))
(Path(__file__).parent / "batch" / "expected.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print({k: len(v) for k, v in out.items()})
