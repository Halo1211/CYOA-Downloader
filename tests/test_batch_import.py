import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cyoa_downloader.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cyoa_downloader_under_test_batch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_txt_batch_import_preserves_legacy_internal_mode(tmp_path):
    mod = load_module()
    batch = tmp_path / "batch.txt"
    batch.write_text("https://example.com/cyoa | Name | website_zip\n", encoding="utf-8")
    items = mod.import_queue_items_from_file(str(batch))
    assert items == [{"url": "https://example.com/cyoa", "filename": "Name", "mode": "website_zip"}]


def test_txt_batch_import_accepts_icc_alias(tmp_path):
    mod = load_module()
    batch = tmp_path / "batch.txt"
    batch.write_text("https://example.com/cyoa | Name | icc_folder\n", encoding="utf-8")
    items = mod.import_queue_items_from_file(str(batch))
    # TXT mode normalization happens in CLI/GUI processing; value remains readable.
    assert items[0]["mode"] == "icc_folder"
