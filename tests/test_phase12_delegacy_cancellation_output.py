import ast
import tempfile
import time
from pathlib import Path

import pytest

import cyoa_downloader
from cyoa_downloader_app.core import cancellation, progress, output, atomic_io

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


def test_cancellation_event_helpers_moved_out_of_legacy():
    assert cyoa_downloader._emit_progress_event is cancellation._emit_progress_event
    assert cyoa_downloader._cancel_requested is cancellation._cancel_requested
    assert cyoa_downloader._raise_if_cancelled is cancellation._raise_if_cancelled
    assert cyoa_downloader._cancel_aware_sleep is cancellation._cancel_aware_sleep

    events = []
    cancellation.set_progress_event_sink(events.append)
    cancellation._emit_progress_event("unit", value=123)
    cancellation.clear_progress_event_sink()
    assert events and events[0]["type"] == "unit" and events[0]["value"] == 123

    names = _legacy_defined_symbols()
    for name in {"_emit_progress_event", "_cancel_requested", "_raise_if_cancelled", "_cancel_aware_sleep"}:
        assert name not in names


def test_download_telemetry_moved_out_of_legacy():
    assert cyoa_downloader.DownloadTelemetry is progress.DownloadTelemetry
    telemetry = progress.DownloadTelemetry()
    telemetry.apply({"type": "queue_started", "total_jobs": 1, "time": time.monotonic()})
    telemetry.apply({"type": "job_started", "job_index": 1, "total_jobs": 1, "mode": "zip", "source_url": "https://example.com"})
    telemetry.apply({"type": "file_started", "name": "asset.png", "total_bytes": 100})
    telemetry.apply({"type": "file_progress", "downloaded": 40, "total": 100})
    snap = telemetry.snapshot()
    assert snap["state"] == progress.DownloadState.DOWNLOADING.value
    assert snap["file_downloaded"] == 40
    assert snap["current_file"] == "asset.png"
    assert "DownloadTelemetry" not in _legacy_defined_symbols()


def test_output_helpers_moved_out_of_legacy():
    assert cyoa_downloader.prepare_clean_output_folder is output.prepare_clean_output_folder
    assert cyoa_downloader._cleanup_recent_part_files is output._cleanup_recent_part_files
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "out"
        root.mkdir()
        part = root / "x.part"
        part.write_text("partial", encoding="utf-8")
        assert output._cleanup_recent_part_files(str(root), time.time() - 1) == 1
        assert not part.exists()
    names = _legacy_defined_symbols()
    assert "prepare_clean_output_folder" not in names
    assert "_cleanup_recent_part_files" not in names


def test_content_length_validator_moved_out_of_legacy():
    assert cyoa_downloader.validate_response_content_length is atomic_io.validate_response_content_length

    class Resp:
        headers = {"Content-Length": "4"}

    assert atomic_io.validate_response_content_length(Resp(), 4) == 4
    with pytest.raises(IOError):
        atomic_io.validate_response_content_length(Resp(), 3)
    assert "validate_response_content_length" not in _legacy_defined_symbols()
