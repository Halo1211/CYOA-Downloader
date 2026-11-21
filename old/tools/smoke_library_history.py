"""Exercise active library/history panels in real Tk, without user data or network."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_library_history import descendants


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix="cyoa-library-smoke-") as profile, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USERPROFILE": profile}))
        import customtkinter as ctk

        from cyoa_downloader_app.gui import app, final_behaviors
        from cyoa_downloader_app.gui.library_panel import open_library_panel
        from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

        owner_thread = threading.get_ident()
        calls, failures = [], []
        projects = [{"id": str(i), "name": f"Project {i:05}", "source_url": f"https://example.invalid/{i}",
                     "file_path": ""} for i in range(1000)]
        projects[-1]["source_url"] = ""
        projects[-1]["file_path"] = "fixture.json"
        history = {f"https://example.invalid/{i}": {"success": True} for i in range(1000)}
        results = [{"url": url, "name": f"Project {i:05}", "status": ("current", "updated", "error", "unreachable")[i % 4]}
                   for i, url in enumerate(history)]
        def read(value):
            assert threading.get_ident() != owner_thread
            calls.append(True)
            time.sleep(.05)
            return value
        def check(items, progress_cb=None, cancel_event=None):
            assert threading.get_ident() != owner_thread
            if failures:
                raise OSError(failures.pop())
            for i in range(1000):
                progress_cb(i + 1, 1000)
            return results
        stack.enter_context(patch.object(app, "_list_cyoa_manager_projects", lambda *a: read(projects)))
        stack.enter_context(patch.object(final_behaviors, "_load_history", lambda: read(history)))
        stack.enter_context(patch.object(final_behaviors, "_batch_check_updates", check))
        root = ctk.CTk()
        root.withdraw()
        root.attributes("-alpha", 0.0)
        gui = CYOADownloaderGUI(root)
        gui._language = "en"
        root.geometry("1280x900+20+20")
        root.deiconify()
        root.lower()
        original_make = gui._make_singleton_window
        def make(key):
            win = original_make(key)
            if win:
                win.attributes("-alpha", 0.0)
                win.lower()
            return win
        gui._make_singleton_window = make
        errors = []
        root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))

        def wait(predicate, timeout=5):
            deadline = time.perf_counter() + timeout
            def poll():
                if errors or predicate() or time.perf_counter() > deadline:
                    root.quit()
                else:
                    root.after(5, poll)
            root.after(1, poll)
            root.mainloop()
            assert not errors, errors
            assert predicate(), "Timed out"

        def pump(seconds=.15):
            until = time.perf_counter() + seconds
            wait(lambda: time.perf_counter() >= until)

        def button(win, text):
            return next(w for w in descendants(win) if isinstance(w, ctk.CTkButton) and w.cget("text") == text)

        def labels(win):
            return [str(w.cget("text")) for w in descendants(win) if isinstance(w, ctk.CTkLabel)]

        def loaded(win):
            return not win._panel_task.busy and win._panel_pager.ready

        try:
            pump(.3)
            gui._cyoa_manager_panel()
            manager = gui._singleton_windows["cyoa_manager"]
            button(manager, "Open").invoke()
            win = gui._singleton_windows["cyoa_manager_library"]
            wait(lambda: loaded(win))
            pager = win._panel_pager
            assert len(pager.items) == 1000 and len(pager.visible) == 12
            checks.append("active Manager action loads off Tk and bounds cards to 12")
            checkbox = next(w for w in descendants(pager.visible[0]) if isinstance(w, ctk.CTkCheckBox))
            checkbox.toggle()
            pager.next.invoke()
            wait(lambda: pager.ready)
            pager.previous.invoke()
            wait(lambda: pager.ready)
            checkbox = next(w for w in descendants(pager.visible[0]) if isinstance(w, ctk.CTkCheckBox))
            assert checkbox.get() == 1
            search = next(w for w in descendants(win) if isinstance(w, ctk.CTkEntry))
            search.insert(0, "00999")
            wait(lambda: pager.ready)
            assert len(pager.items) == 1
            button(win, "Select All Matches").invoke()
            wait(lambda: pager.ready)
            button(win, "Queue URLs").invoke()
            wait(lambda: not gui._queue_import_busy)
            assert len(gui._queue_data) == 1 and gui._queue_data[0]["url"].endswith("/0")
            checks.append("selection survives page/search and local-only selections are skipped")
            button(manager, "Open").invoke()
            win = gui._singleton_windows["cyoa_manager_library"]
            wait(lambda: loaded(win))
            button(win, "Select All Matches").invoke()
            wait(lambda: win._panel_pager.ready)
            button(win, "Queue URLs").invoke()
            wait(lambda: not gui._queue_import_busy)
            assert len(gui._queue_data) == 1000
            checks.append("Select All Matches queues all 999 URLs across pages through bounded commits")

            gui._batch_update_panel()
            win = gui._singleton_windows["batch_check_center"]
            wait(lambda: loaded(win))
            before = len(calls)
            gui._batch_update_panel()
            assert len(calls) == before
            button(win, "Errors").invoke()
            wait(lambda: win._panel_pager.ready)
            assert len(win._panel_pager.items) == 500
            assert {r["status"] for r in win._panel_pager.items} == {"error", "unreachable"}
            checks.append("history singleton avoids duplicate reads and Errors includes unreachable")
            button(win, "Queue Updated").invoke()
            wait(lambda: not gui._queue_import_busy)
            assert len(gui._queue_data) == 1250
            checks.append("Queue Updated includes results beyond the visible page")
            failures.append("fixture read failure")
            button(win, "Run Check").invoke()
            wait(lambda: loaded(win))
            assert any("fixture read failure" in text for text in labels(win))
            pump()
            assert any("fixture read failure" in text for text in labels(win))
            button(win, "Run Check").invoke()
            wait(lambda: loaded(win))
            assert len(win._panel_pager.items) == 500  # Preserve selected filter on retry.
            checks.append("failed check stays failed and retry restores the selected filter")
            win.destroy()

            entered, release = threading.Event(), threading.Event()
            def blocked():
                entered.set()
                release.wait(5)
                return history
            with patch.object(final_behaviors, "_load_history", blocked):
                gui._batch_update_panel()
                old = gui._singleton_windows["batch_check_center"]
                wait(entered.is_set)
                old_task = old._panel_task
                old.destroy()
            gui._batch_update_panel()
            win = gui._singleton_windows["batch_check_center"]
            release.set()
            wait(lambda: loaded(win))
            assert old_task.closed and old_task.cancelled.is_set()
            assert len(win._panel_pager.items) == 1000
            win.destroy()
            checks.append("close while reading discards old completion after reopening")

            prepared, served = [], []
            def prepare(path):
                assert threading.get_ident() != owner_thread
                time.sleep(.05)
                prepared.append(path)
                return "fixture-viewer"
            with patch.object(gui, "_start_server", lambda **kw: served.append(kw)):
                open_library_panel(gui, manager, lambda: read(projects[-1:]), prepare, lambda: True)
                win = gui._singleton_windows["cyoa_manager_library"]
                wait(lambda: loaded(win))
                button(win, "Select All Matches").invoke()
                wait(lambda: win._panel_pager.ready)
                button(win, "Serve Local").invoke()
                wait(lambda: served)
                assert prepared == ["fixture.json"] and served == [{"folder": "fixture-viewer"}]
            checks.append("local viewer preparation runs off Tk and starts Serve on completion")

            button(manager, "Open").invoke()
            win = gui._singleton_windows["cyoa_manager_library"]
            wait(lambda: loaded(win))
            search = next(w for w in descendants(win) if isinstance(w, ctk.CTkEntry))
            search.insert(0, "pending")
            search_variable = search.cget("textvariable")
            manager.destroy()
            pump(.25)
            assert win._panel_task.closed and win._panel_pager.closed, (win._panel_task.closed, win._panel_pager.closed)
            assert not search_variable.trace_info()
            assert not errors
            checks.append("closing Manager cancels child search debounce, card build and task poll")
        finally:
            gui._v46_finish_close()
    args.report.write_text(json.dumps({"checks": checks, "passed": len(checks)}, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
