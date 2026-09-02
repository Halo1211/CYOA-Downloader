import subprocess
import sys
import tempfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.app_info import _APP_VERSION
from cyoa_downloader_app.constants.assets import AUDIO_EXTENSIONS, IMAGE_FIELDS
from cyoa_downloader_app.core.atomic_io import atomic_write_text
from cyoa_downloader_app.core.paths import _safe_archive_rel_path, _safe_join
from cyoa_downloader_app.importers.batch import (
    _derive_mode_flags,
    _normalize_batch_mode,
)


def test_phase1_facade_names_still_match_modules():
    assert cyoa_downloader._APP_VERSION == _APP_VERSION
    assert _APP_VERSION == "1.0.8"
    assert cyoa_downloader.IMAGE_FIELDS is IMAGE_FIELDS
    assert ".mp3" in AUDIO_EXTENSIONS
    assert cyoa_downloader._derive_mode_flags is _derive_mode_flags


def test_phase1_path_and_archive_guards():
    with tempfile.TemporaryDirectory() as tmp:
        out = _safe_join(tmp, "../CON/file?.png")
        assert str(Path(out).resolve()).startswith(str(Path(tmp).resolve()))
        assert "CON" not in Path(out).parts[-2] or Path(out).parts[-2].startswith("_")
    assert _safe_archive_rel_path("folder/file.txt") == "folder/file.txt"
    try:
        _safe_archive_rel_path("../evil.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("archive traversal was not rejected")
    for device_name in ("CON", "NUL", "folder/COM1.txt"):
        try:
            _safe_archive_rel_path(device_name)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Windows device archive member was accepted: {device_name}")


def test_phase1_batch_modes_and_atomic_write():
    assert _derive_mode_flags("cyoap_vue")["engine"] == "cyoap_vue"
    assert _normalize_batch_mode("icc-folder") == "website_folder"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "a" / "note.txt"
        atomic_write_text(str(target), "ok")
        assert target.read_text(encoding="utf-8") == "ok"


def test_cli_version_is_available_without_starting_a_download():
    completed = subprocess.run(
        [sys.executable, "cyoa_downloader.py", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0
    assert "CYOA Downloader 1.0.8" in completed.stdout
    assert "CYOA-v1.0.8" in completed.stdout
