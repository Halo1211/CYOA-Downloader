"""Export checkpoint naming and payload parity data. Run from the repository root (Windows)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cyoa_downloader_app.storage import archive_checkpoint as ac  # noqa: E402

ROOT = Path("C:/__cyoa_fixture_root__")
ac._checkpoint_root = lambda: ROOT
folders = [
    r"C:\__cyoa_missing__\Out\Site",
    "C:/__cyoa_missing__/Out/Site",
    r"C:\__cyoa_missing__\OUT\site\\",
    r"C:\__cyoa_missing__\名前\Ünïcode Site",
    r"C:\__cyoa_missing__\a b\c.d",
]
names = [{"folder": f, "name": Path(ac.ArchiveCheckpoint.path_for(f)).name} for f in folders]
payloads = []
for meta in [{}, {"mime": "image/png"}, {"etag": "\"x\"", "note": "é😀\n"}, {"size": 1, "nested": {"a": [1, None, True]}}]:
    data = dict(meta, local="dir/a b.png", size=7, sha256="ab" * 32)
    payloads.append({"meta": meta, "json": json.dumps(data)})
out = Path("rust/fixtures/checkpoint")
out.mkdir(parents=True, exist_ok=True)
(out / "cases.json").write_text(json.dumps({"names": names, "payloads": payloads}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
print(f"names {len(names)} payloads {len(payloads)}")
