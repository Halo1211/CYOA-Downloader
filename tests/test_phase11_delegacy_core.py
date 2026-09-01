import ast
import io
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.core import progress, url_utils
from cyoa_downloader_app.core.archive import validate_zip_archive
from cyoa_downloader_app.core.atomic_io import interprocess_file_lock

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


def _legacy_defined_symbols():
    tree = ast.parse(LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_progress_helpers_moved_out_of_legacy():
    assert cyoa_downloader.DownloadState is progress.DownloadState
    assert cyoa_downloader.DownloadCancelledError is progress.DownloadCancelledError
    assert cyoa_downloader.format_bytes is progress.format_bytes
    assert cyoa_downloader.calculate_eta is progress.calculate_eta
    assert progress.format_bytes(1536) == "1.50 KB"
    assert progress.format_speed(2048) == "2.00 KB/s"
    assert progress.calculate_eta(100, 10, sample_count=3) == 10
    names = _legacy_defined_symbols()
    for name in {
        "DownloadState", "DownloadCancelledError", "format_bytes",
        "format_speed", "format_duration", "calculate_smoothed_speed",
        "calculate_eta", "calculate_stage_progress",
    }:
        assert name not in names


def test_url_helpers_moved_out_of_legacy():
    assert cyoa_downloader.canonicalize_url is url_utils.canonicalize_url
    assert cyoa_downloader.truncate_display_url is url_utils.truncate_display_url
    assert url_utils.canonicalize_url("HTTPS://Example.COM:443/a/../b/") == "https://example.com/b/"
    assert "…" in url_utils.truncate_display_url("https://example.com/" + "x" * 80, 32)
    names = _legacy_defined_symbols()
    assert "canonicalize_url" not in names
    assert "truncate_display_url" not in names


def test_probable_url_rejects_scheme_only_whitespace_and_invalid_ports():
    assert url_utils.is_probable_url("https://example.test/path")
    assert not url_utils.is_probable_url("https://")
    assert not url_utils.is_probable_url("https://exa mple.test/path")
    assert not url_utils.is_probable_url("https://example.test:not-a-port/path")
    assert not url_utils.is_probable_url("javascript:alert(1)")


def test_archive_validator_moved_out_of_legacy_and_rejects_traversal():
    assert cyoa_downloader.validate_zip_archive is validate_zip_archive
    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("safe/file.txt", "ok")
    assert validate_zip_archive(good.getvalue()) == {"members": 1, "total_size": 2}

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "no")
    try:
        validate_zip_archive(bad.getvalue())
    except ValueError as exc:
        assert "Unsafe archive path" in str(exc)
    else:
        raise AssertionError("unsafe archive path was accepted")

    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as zf:
        zf.writestr("one.txt", "1")
        zf.writestr("two.txt", "2")
    try:
        validate_zip_archive(many.getvalue(), max_members=1)
    except ValueError as exc:
        assert "too many members" in str(exc)
    else:
        raise AssertionError("member limit was not enforced")

    names = _legacy_defined_symbols()
    assert "validate_zip_archive" not in names


def test_interprocess_file_lock_blocks_another_python_process(tmp_path):
    target = tmp_path / "state.json"
    marker = tmp_path / "child-entered"
    code = (
        "from pathlib import Path\n"
        "from cyoa_downloader_app.core.atomic_io import interprocess_file_lock\n"
        f"with interprocess_file_lock({str(target)!r}, timeout=5):\n"
        f"    Path({str(marker)!r}).write_text('entered', encoding='utf-8')\n"
    )

    with interprocess_file_lock(str(target), timeout=2):
        process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT)
        time.sleep(0.25)
        assert not marker.exists()

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert marker.read_text(encoding="utf-8") == "entered"
