"""Report output is complete, atomic and cancellable independently of Tk."""

import csv
import threading

import pytest

from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.gui.results_panel import failure_text, write_results_csv


def test_csv_keeps_full_unicode_fields_and_ignores_extra_metadata(tmp_path):
    path = tmp_path / "results.csv"
    rows = [{"status": "FAIL", "url": "https://fixture.invalid/a?q=雪", "filename": "a,\"b\n雪",
             "error": "long\nreason", "queue_id": "not-a-column"}]
    write_results_csv(path, rows, threading.Event())
    with path.open(newline="", encoding="utf-8") as handle:
        actual = list(csv.DictReader(handle))
    assert actual[0]["filename"] == rows[0]["filename"]
    assert actual[0]["error"] == rows[0]["error"]
    assert "queue_id" not in actual[0]
    assert not list(tmp_path.glob("*.part"))


def test_cancelled_csv_preserves_old_file_and_removes_temporary_output(tmp_path):
    path = tmp_path / "results.csv"
    path.write_bytes(b"old export")
    cancel = threading.Event()

    def rows():
        yield {"url": "first"}
        cancel.set()
        yield {"url": "second"}

    with pytest.raises(DownloadCancelledError):
        write_results_csv(path, rows(), cancel)
    assert path.read_bytes() == b"old export"
    assert not list(tmp_path.glob("*.part"))


def test_failed_csv_preserves_old_export(tmp_path):
    path = tmp_path / "results.csv"
    path.write_bytes(b"old export")

    def rows():
        yield {"url": "first"}
        raise OSError("fixture disk failure")

    with pytest.raises(OSError):
        write_results_csv(path, rows(), threading.Event())
    assert path.read_bytes() == b"old export"
    assert not list(tmp_path.glob("*.part"))


def test_failure_copy_preserves_job_and_asset_sections():
    groups = {"cyoa_failed": [{"url": "job", "error": "reason"}], "asset_failed": [{"url": "image", "error": "missing"}]}
    assert failure_text(groups, threading.Event()) == "FAILED CYOA\njob\treason\n\nFAILED ASSETS / IMAGES\nimage\tmissing"
