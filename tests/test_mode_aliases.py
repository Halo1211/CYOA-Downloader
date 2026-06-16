import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cyoa_downloader.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cyoa_downloader_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_icc_cli_maps_to_website_zip(monkeypatch, tmp_path):
    mod = load_module()
    captured = {}

    def fake_run_download(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mod, "run_download", fake_run_download)
    monkeypatch.setattr(sys, "argv", ["cyoa_downloader.py", "--icc", "https://example.com", "-o", str(tmp_path)])
    mod.main()

    assert captured["website_output"] is True
    assert captured["website_zip_output"] is True
    assert captured["pure_website"] is False


def test_icc_folder_cli_maps_to_website_folder(monkeypatch, tmp_path):
    mod = load_module()
    captured = {}

    def fake_run_download(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mod, "run_download", fake_run_download)
    monkeypatch.setattr(sys, "argv", ["cyoa_downloader.py", "--icc-folder", "https://example.com", "-o", str(tmp_path)])
    mod.main()

    assert captured["website_output"] is True
    assert captured["website_zip_output"] is False
    assert captured["pure_website"] is False
