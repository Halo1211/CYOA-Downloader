from pathlib import Path
import importlib

import cyoa_downloader
from cyoa_downloader_app.runtime import surface

ROOT = Path(__file__).resolve().parents[1]


def test_phase69_legacy_file_is_deleted_and_surface_exists():
    assert not (ROOT / "cyoa_downloader_app" / "legacy.py").exists()
    assert (ROOT / "cyoa_downloader_app" / "runtime" / "surface.py").exists()
    assert hasattr(surface, "run_download")
    assert hasattr(surface, "CYOADownloaderGUI")


def test_phase70_public_facade_uses_surface_not_legacy_module():
    compat = importlib.import_module("cyoa_downloader_app.compat")
    assert compat.run_download is surface.run_download
    assert compat.CYOADownloaderGUI is surface.CYOADownloaderGUI
    assert cyoa_downloader.run_download is surface.run_download
    assert cyoa_downloader.CYOADownloaderGUI is surface.CYOADownloaderGUI


def test_phase71_no_code_imports_deleted_legacy_module():
    offenders = []
    for path in (ROOT / "cyoa_downloader_app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "importlib.import_module(\"cyoa_downloader_app.legacy\")" in source:
            offenders.append(path.relative_to(ROOT).as_posix())
        if "from .. import legacy" in source or "from ... import legacy" in source:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_phase72_compatibility_surface_keeps_core_private_names():
    for name in [
        "_derive_mode_flags",
        "_cache_load",
        "_cache_get",
        "_v25_safe_after_widget",
        "try_decode_bytes",
        "fetch_response",
        "process_images",
        "WebsiteDownloader",
    ]:
        assert hasattr(surface, name), name
        assert hasattr(cyoa_downloader, name), name
