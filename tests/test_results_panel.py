"""Real report widgets, filters and CSV work stay bounded and off Tk."""

import csv
import os
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CYOA_GUI_SMOKE") != "1", reason="requires real Tk")


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


@pytest.fixture(scope="module")
def report_gui(tmp_path_factory):
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("USERPROFILE", str(tmp_path_factory.mktemp("report-profile")))
        root = ctk.CTk()
        root.attributes("-alpha", 0)
        gui = CYOADownloaderGUI(root)
        gui._language = "en"
        gui._last_results = [
            {"status": ("OK", "SKIP", "FAIL")[i % 3], "url": f"https://fixture.invalid/{i}",
             "mode": "website_folder", "filename": f"Story {i:04} 雪", "error": "fixture" if i % 3 == 2 else ""}
            for i in range(103)
        ]
        errors = []
        root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))

        def pump(predicate):
            deadline = time.perf_counter() + 10
            while not predicate():
                assert time.perf_counter() < deadline, "report task timed out"
                root.update()
                time.sleep(.005)
            root.update()
            assert not errors, errors

        def button(text):
            win = gui._singleton_windows["reports_center"]
            return next(w for w in children(win) if isinstance(w, ctk.CTkButton) and w.cget("text") == text)

        yield root, gui, pump, button
        gui._v46_finish_close()


def test_report_widgets_and_fonts_are_bounded_and_filters_keep_all_records(report_gui):
    root, gui, pump, button = report_gui
    before = set(root.tk.call("font", "names"))
    gui._show_results()
    win = gui._singleton_windows["reports_center"]
    win.attributes("-alpha", 0)
    assert len(set(root.tk.call("font", "names")) - before) < 100
    pager = win._panel_pager
    pump(lambda: pager.ready and not win._panel_task.busy)
    assert len(pager.items) == 103
    assert len(pager.visible) <= pager.PAGE_SIZE
    pager.show(1)
    pump(lambda: pager.ready)
    assert pager.page == 1 and len(pager.visible) == pager.PAGE_SIZE
    button("CYOA Failed").invoke()
    pump(lambda: pager.ready)
    assert len(pager.items) == 34
    assert all(row["status"] == "FAIL" for row in pager.items)
    button("CYOA Success").invoke()
    pump(lambda: pager.ready)
    assert len(pager.items) == 69
    assert {row["status"] for row in pager.items} == {"OK", "SKIP"}


def test_csv_exports_the_full_snapshot_in_a_worker_even_with_a_filter(report_gui, monkeypatch, tmp_path):
    from tkinter import filedialog

    _root, gui, pump, button = report_gui
    win = gui._singleton_windows["reports_center"]
    destination = tmp_path / "all-results.csv"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kwargs: str(destination))
    ui_thread = threading.get_ident()
    native = csv.DictWriter.writerow
    threads = []

    def write(writer, row):
        threads.append(threading.get_ident())
        assert threading.get_ident() != ui_thread, "CSV serialization/writes ran on Tk"
        return native(writer, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", write)
    button("Export CSV").invoke()
    pump(lambda: destination.exists() and not getattr(win, "_panel_task", None).busy)
    with destination.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert len(records) == 103
    assert records[12]["filename"] == "Story 0012 雪"
    assert threads and all(value != ui_thread for value in threads)


def test_report_membership_is_frozen_before_background_preparation(report_gui, monkeypatch):
    from cyoa_downloader_app.gui import final_behaviors

    _root, gui, pump, _button = report_gui
    gui._singleton_windows["reports_center"].destroy()
    entered, release = threading.Event(), threading.Event()
    native = final_behaviors._v24_result_rows
    ui_thread = threading.get_ident()

    def prepare(snapshot):
        assert threading.get_ident() != ui_thread, "report preparation ran on Tk"
        entered.set()
        assert release.wait(3)
        return native(snapshot)

    monkeypatch.setattr(final_behaviors, "_v24_result_rows", prepare)
    gui._show_results()
    win = gui._singleton_windows["reports_center"]
    win.attributes("-alpha", 0)
    assert entered.wait(2)
    gui._last_results.append({"url": "late", "status": "OK"})
    try:
        release.set()
        pump(lambda: not win._panel_task.busy and win._panel_pager.ready)
        assert len(win._panel_pager.items) == 103
    finally:
        gui._last_results.pop()
        release.set()


def test_closing_during_preparation_cancels_late_ui_delivery(report_gui, monkeypatch):
    from cyoa_downloader_app.gui import final_behaviors

    _root, gui, pump, _button = report_gui
    gui._singleton_windows["reports_center"].destroy()
    entered, release = threading.Event(), threading.Event()
    native = final_behaviors._v24_result_rows

    def prepare(snapshot):
        entered.set()
        assert release.wait(3)
        return native(snapshot)

    monkeypatch.setattr(final_behaviors, "_v24_result_rows", prepare)
    gui._show_results()
    win = gui._singleton_windows["reports_center"]
    task = win._panel_task
    assert entered.wait(2)
    win.destroy()
    release.set()
    pump(lambda: not task.thread.is_alive())
    assert task.closed and task.cancelled.is_set() and task.result is None


def test_closing_during_export_keeps_the_previous_csv(report_gui, monkeypatch, tmp_path):
    from tkinter import filedialog

    from cyoa_downloader_app.gui import results_panel

    _root, gui, pump, button = report_gui
    gui._show_results()
    win = gui._singleton_windows["reports_center"]
    win.attributes("-alpha", 0)
    pump(lambda: not win._panel_task.busy and win._panel_pager.ready)
    path = tmp_path / "existing.csv"
    path.write_bytes(b"previous export")
    entered, release = threading.Event(), threading.Event()
    native = results_panel.write_results_csv

    def write(*values):
        entered.set()
        assert release.wait(3)
        return native(*values)

    monkeypatch.setattr(results_panel, "write_results_csv", write)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kw: str(path))
    button("Export CSV").invoke()
    assert entered.wait(2)
    task = win._panel_task
    win.destroy()
    release.set()
    pump(lambda: not task.thread.is_alive())
    assert path.read_bytes() == b"previous export"
    assert not list(tmp_path.glob("*.part"))
