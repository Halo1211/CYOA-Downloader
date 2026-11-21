"""The real Tk loop remains usable while run identities are being prepared."""

import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CYOA_GUI_SMOKE") != "1", reason="requires real Tk")


def test_cancel_and_edit_remain_available_during_worker_preparation(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import customtkinter as ctk

    from cyoa_downloader_app.gui import final_behaviors
    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI
    from cyoa_downloader_app.storage import resume

    root = ctk.CTk()
    root.attributes("-alpha", 0)
    gui = CYOADownloaderGUI(root)
    gui._queue_data.extend([
        {"url": "https://fixture.invalid/game", "filename": "First 雪", "mode": "", "_queue_id": "one"},
        {"url": "https://fixture.invalid/game", "filename": "Second", "mode": "zip", "_queue_id": "two"},
    ])
    gui._queue_view.changed()
    gui._outdir_var.set(str(tmp_path / "output"))
    entered, release = threading.Event(), threading.Event()
    native_key = resume.resume_job_key
    ui_thread = threading.get_ident()
    observed, errors = [], []
    root.report_callback_exception = lambda *_exc: errors.append(str(_exc[1]))

    def key(*values):
        assert threading.get_ident() != ui_thread, "resume preparation blocked the Tk callback"
        observed.append(values)
        entered.set()
        assert release.wait(3)
        return native_key(*values)

    native_sink = gui._v46_set_event_sink
    def sink():
        if threading.get_ident() == ui_thread:
            native_sink()
        else:
            raise OSError("fixture: never enter network setup")

    monkeypatch.setattr(resume, "resume_job_key", key)
    monkeypatch.setattr(gui, "_v46_set_event_sink", sink)
    monkeypatch.setattr(gui, "_show_results", lambda: None)
    monkeypatch.setattr(final_behaviors, "_send_desktop_notification", lambda *_a: None)
    try:
        root.update()
        gui._start()
        assert entered.wait(2)
        serviced = []
        root.after(0, lambda: serviced.append(True))
        root.update()
        assert serviced and gui._is_running
        gui._queue_data[0]["filename"] = "Edited while preparing"
        gui._v46_cancel()
        release.set()
        deadline = time.perf_counter() + 5
        while gui._is_running or gui._worker_thread.is_alive():
            assert time.perf_counter() < deadline
            root.update()
            time.sleep(.005)
        assert observed[0][1] == "First 雪"
        assert len(gui._queue_data) == 2 and gui._queue_data[0]["filename"] == "Edited while preparing"
        assert not errors, errors
    finally:
        release.set()
        gui._v46_finish_close()
