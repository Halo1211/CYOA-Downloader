import tempfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.app_info import _APP_VERSION
from cyoa_downloader_app.constants.assets import IMAGE_FIELDS, AUDIO_EXTENSIONS
from cyoa_downloader_app.core.paths import _safe_join, _safe_archive_rel_path
from cyoa_downloader_app.core.atomic_io import atomic_write_text
from cyoa_downloader_app.importers.batch import _derive_mode_flags, _normalize_batch_mode


def test_phase1_facade_names_still_match_modules():
    assert cyoa_downloader._APP_VERSION == _APP_VERSION
    assert _APP_VERSION == "1.0.5"
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


def test_phase1_batch_modes_and_atomic_write():
    assert _derive_mode_flags("cyoap_vue")["engine"] == "cyoap_vue"
    assert _normalize_batch_mode("icc-folder") == "website_folder"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "a" / "note.txt"
        atomic_write_text(str(target), "ok")
        assert target.read_text(encoding="utf-8") == "ok"
