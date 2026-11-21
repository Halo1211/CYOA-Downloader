"""Real Tk panel benchmark with synthetic records and isolated storage."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--delay", type=float, default=.25)
    parser.add_argument("--expect-async", action="store_true")
    args = parser.parse_args()
    samples = []
    with tempfile.TemporaryDirectory(prefix="cyoa-panel-benchmark-") as profile, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USERPROFILE": profile}))
        import customtkinter as ctk

        from cyoa_downloader_app.gui import app, final_behaviors
        from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

        tk_thread = threading.get_ident()
        reads = []
        projects = [{"id": str(i), "name": f"Project {i:05}", "source_url": f"https://example.invalid/{i}",
                     "file_path": ""} for i in range(args.records)]
        history = {p["source_url"]: {"success": True, "filename": p["name"]} for p in projects}
        results = [{"url": p["source_url"], "name": p["name"], "status": "updated" if i % 2 else "current",
                    "reason": "fixture"} for i, p in enumerate(projects)]

        def read(value):
            reads.append(threading.get_ident())
            if args.expect_async:
                assert threading.get_ident() != tk_thread, "Panel I/O ran on Tk"
            time.sleep(args.delay)
            return value

        def check(items, progress_cb=None, **kwargs):
            for i in range(len(items)):
                if progress_cb:
                    progress_cb(i + 1, len(items))
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
        callback_errors = []
        root.report_callback_exception = lambda *exc: callback_errors.append(str(exc[1]))
        gaps, timer = [], [None]
        previous = [time.perf_counter()]

        def heartbeat():
            now = time.perf_counter()
            gaps.append((now - previous[0]) * 1000)
            previous[0] = now
            timer[0] = root.after(5, heartbeat)

        def pump_until(predicate, timeout=90):
            deadline = time.perf_counter() + timeout
            timed_out = []
            def poll():
                if callback_errors or predicate():
                    root.quit()
                elif time.perf_counter() > deadline:
                    timed_out.append(True)
                    root.quit()
                else:
                    root.after(5, poll)
            root.after(1, poll)
            root.mainloop()
            if callback_errors:
                raise RuntimeError(callback_errors)
            if timed_out:
                raise RuntimeError("Panel benchmark timed out")

        def settle(seconds=.2):
            until = time.perf_counter() + seconds
            pump_until(lambda: time.perf_counter() >= until)

        def button(owner, label):
            return next(w for w in descendants(owner) if isinstance(w, ctk.CTkButton) and w.cget("text") == label)

        def phase(kind, action, ready):
            gaps.clear()
            previous[0] = time.perf_counter()
            elapsed = []
            def invoke():
                start = time.perf_counter()
                action()
                elapsed.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            root.after(20, invoke)
            pump_until(lambda: elapsed and ready())
            settle()
            return {"operation": kind, "callback_ms": elapsed[0], "total_ms": (time.perf_counter() - start) * 1000,
                    "heartbeat_max_ms": max(gaps), "heartbeat_p95_ms": sorted(gaps)[int(.95 * (len(gaps)-1))]}

        try:
            settle(.4)
            gui._cyoa_manager_panel()
            manager = gui._singleton_windows["cyoa_manager"]
            # The manager's library action is its first Open button.
            library_button = button(manager, "Open")
            heartbeat()
            for run in range(args.runs):
                for kind in ("library", "history"):
                    before_windows = set(root.winfo_children())
                    def owner(before_windows=before_windows):
                        return next((w for w in descendants(root) if isinstance(w, ctk.CTkToplevel)
                                     and w not in before_windows and w is not manager), None)
                    def ready(kind=kind, owner=owner):
                        win = owner()
                        if win is None:
                            return False
                        pager = getattr(win, "_panel_pager", None)
                        if pager is not None:
                            return pager.ready and not win._panel_task.busy
                        texts = [w.cget("text") for w in descendants(win) if isinstance(w, ctk.CTkLabel)]
                        return any(str(t).startswith("Done") for t in texts) if kind == "history" else any("Project" in str(t) for t in texts)
                    action = library_button.invoke if kind == "library" else gui._batch_update_panel
                    row = phase(kind + "_open", action, ready)
                    win = owner()
                    row["cards"] = len(getattr(getattr(win, "_panel_pager", None), "visible", [])) or args.records
                    samples.append({"run": run, **row})
                    if kind == "library":
                        search = next(w for w in descendants(win) if isinstance(w, ctk.CTkEntry))
                        samples.append({"run": run, **phase("library_search", lambda search=search: search.insert(0, "Project"), ready)})
                    else:
                        samples.append({"run": run, **phase("history_filter", button(win, "Updated").invoke, ready)})
                    win.destroy()
                    settle(.05)
        finally:
            if timer[0]:
                root.after_cancel(timer[0])
            gui._v46_finish_close()
    summary = {kind: {key: statistics.median(row[key] for row in samples if row["operation"] == kind)
                      for key in ("callback_ms", "total_ms", "heartbeat_max_ms", "heartbeat_p95_ms")}
               for kind in sorted({row["operation"] for row in samples})}
    report = {"records": args.records, "runs": args.runs, "simulated_read_seconds": args.delay,
              "io_on_tk": sum(t == tk_thread for t in reads), "summary": summary, "samples": samples}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
