import logging
import queue
import threading
from collections import deque
from types import SimpleNamespace

from cyoa_downloader_app.core.progress import DownloadTelemetry
from cyoa_downloader_app.gui.final_behaviors import (
    _v24_result_is_failed,
    _v24_result_rows,
)
from cyoa_downloader_app.gui.telemetry_log import _V46TelemetryLogHandler


def test_asset_failure_survives_successful_parent_job_for_results():
    telemetry = DownloadTelemetry()
    telemetry.apply({"type": "queue_started", "total_jobs": 1})
    telemetry.apply({
        "type": "job_started",
        "job_index": 1,
        "total_jobs": 1,
        "source_url": "https://example.test/cyoa/",
        "mode": "website_folder",
    })
    telemetry.apply({
        "type": "file_failed",
        "name": "missing.png",
        "url": "https://example.test/cyoa/missing.png",
        "error": "HTTP 404",
    })
    telemetry.apply({"type": "job_completed"})

    gui = SimpleNamespace(
        _last_results=[{
            "status": "OK",
            "url": "https://example.test/cyoa/",
            "mode": "website_folder",
            "filename": "example",
            "error": "",
        }],
        _v46_telemetry=telemetry,
    )
    rows = _v24_result_rows(gui)
    failed = [row for row in rows if _v24_result_is_failed(row)]

    assert len(rows) == 2
    assert len(failed) == 1
    assert failed[0]["filename"] == "missing.png"
    assert failed[0]["url"] == "https://example.test/cyoa/missing.png"
    assert failed[0]["error"] == "HTTP 404"


def test_results_see_enqueued_failure_before_gui_poller_applies_it():
    gui = SimpleNamespace(
        _last_results=[{"status": "OK", "url": "https://example.test/game/"}],
        _v46_telemetry=DownloadTelemetry(),
        _v46_failure_events=deque([{
            "name": "late.png",
            "url": "https://example.test/game/late.png",
            "error": "timed out",
        }], maxlen=500),
        _v46_failure_events_lock=threading.Lock(),
    )

    failed = [row for row in _v24_result_rows(gui) if _v24_result_is_failed(row)]

    assert len(failed) == 1
    assert failed[0]["filename"] == "late.png"
    assert failed[0]["error"] == "timed out"


def test_failure_details_span_jobs_but_reset_for_new_queue():
    telemetry = DownloadTelemetry()
    telemetry.apply({"type": "queue_started", "total_jobs": 2})
    telemetry.apply({"type": "job_started", "job_index": 1, "total_jobs": 2})
    telemetry.apply({"type": "file_failed", "name": "one.png", "error": "failed one"})
    telemetry.apply({"type": "job_started", "job_index": 2, "total_jobs": 2})

    assert [item["name"] for item in telemetry.failure_details] == ["one.png"]

    telemetry.apply({"type": "queue_started", "total_jobs": 1})
    assert list(telemetry.failure_details) == []


def test_skipped_resume_result_is_not_classified_as_failed():
    assert _v24_result_is_failed({"status": "SKIP"}) is False
    assert _v24_result_is_failed({"status": "OK"}) is False
    assert _v24_result_is_failed({"status": "FAIL"}) is True


def test_cross_only_legacy_failure_keeps_diagnostic_text():
    events = []
    class Gui:
        def _v46_enqueue_progress(self, event):
            events.append(event)

    gui = Gui()
    handler = _V46TelemetryLogHandler(gui)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[2/3] ✗ missing.png",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    failure = events[-1]
    assert failure["type"] == "file_failed"
    assert failure["name"] == "missing.png"
    assert failure["error"] == "[2/3] ✗ missing.png"


def test_successful_asset_name_containing_failed_is_not_misclassified():
    events = []

    class Gui:
        def _v46_enqueue_progress(self, event):
            events.append(event)

    gui = Gui()
    handler = _V46TelemetryLogHandler(gui)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[1/1] ✓ failed_banner.png",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    completed = events[-1]
    assert completed["type"] == "file_completed"
    assert completed["name"] == "failed_banner.png"


def test_saturated_progress_queue_never_evicts_existing_important_event():
    from queue import Full

    from cyoa_downloader_app.gui import final_behaviors

    class SaturatedQueue:
        def __init__(self):
            self.get_called = False

        def put_nowait(self, _event):
            raise Full

        def get_nowait(self):
            self.get_called = True
            raise AssertionError("an existing important event was evicted")

    saturated_queue = SaturatedQueue()
    priority_queue = queue.SimpleQueue()
    gui = SimpleNamespace(
        _v46_progress_queue=saturated_queue,
        _v46_priority_progress_queue=priority_queue,
    )
    expected = {"type": "job_failed", "error": "boom"}

    final_behaviors._v46_enqueue_progress(gui, expected)

    assert saturated_queue.get_called is False
    assert priority_queue.get_nowait() == expected
