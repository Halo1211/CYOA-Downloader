"""Measure real Tk cache callbacks with simulated slow storage, no user cache I/O."""

from __future__ import annotations

import argparse
import ast
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

_CALLBACK_ERRORS = (Exception,)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--control-app", type=Path, help="Trusted source snapshot for the previous inline panel")
    parser.add_argument("--control-final", type=Path, help="Trusted source snapshot for the previous dialog")
    parser.add_argument("--expect-async", action="store_true", help="Require every storage callback to run off Tk")
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="cyoa-cache-panel-") as profile, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USERPROFILE": profile}))
        import customtkinter as ctk

        from cyoa_downloader_app.gui import app, final_behaviors
        from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

        fake_limit = [2048]
        tk_thread = threading.get_ident()

        def check_thread():
            if args.expect_async:
                assert threading.get_ident() != tk_thread, "Cache I/O entered Tk's owner thread"

        def stats():
            check_thread()
            time.sleep(args.delay)
            return {"entries": 12000, "size_mb": 512, "limit_mb": fake_limit[0]}

        def enforce():
            check_thread()
            time.sleep(args.delay)
            return 12

        def clear():
            check_thread()
            time.sleep(args.delay)
            return 12000

        def save(key, value):
            check_thread()
            assert key == "image_cache_max_mb"
            fake_limit[0] = value

        for module in (app, final_behaviors):
            for name, function in (("_cache_stats", stats), ("_enforce_cache_limit", enforce),
                                   ("_clear_image_cache", clear), ("_update_setting", save),
                                   ("_update_settings", lambda updates: (save("image_cache_max_mb", updates["image_cache_max_mb"]), True)[1])):
                stack.enter_context(patch.object(module, name, function, create=True))
        stack.enter_context(patch("tkinter.messagebox.askyesno", lambda *a, **kw: True))
        stack.enter_context(patch("tkinter.messagebox.showerror", lambda *a, **kw: None))
        root = ctk.CTk()
        root.withdraw()
        root.attributes("-alpha", 0.0)
        gui = CYOADownloaderGUI(root)
        gui._language = "en"
        root.geometry("1280x900+20+20")
        root.deiconify()
        root.lower()
        make_window = gui._make_singleton_window

        def hidden_window(key):
            window = make_window(key)
            if window is not None:
                window.attributes("-alpha", 0.0)
                window.lower()
            return window

        gui._make_singleton_window = hidden_window
        panel_function = final_behaviors._v27_cache_manager_panel

        def load_function(path, name, module):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
            namespace = dict(vars(module))
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
            return namespace[name]

        if args.control_app:
            gui._settings_inline_cache = types.MethodType(load_function(args.control_app, "_settings_inline_cache", app), gui)
        if args.control_final:
            panel_function = load_function(args.control_final, "_v27_cache_manager_panel", final_behaviors)
        last_tick = [time.perf_counter()]
        current_gaps = [None]
        running = [True]
        timer = [None]

        def heartbeat():
            now = time.perf_counter()
            if current_gaps[0] is not None:
                current_gaps[0].append((now - last_tick[0]) * 1000)
            last_tick[0] = now
            if running[0]:
                timer[0] = root.after(5, heartbeat)

        def pump_until(predicate, timeout=10):
            deadline = time.perf_counter() + timeout
            while not predicate():
                root.update()
                time.sleep(0.001)
                if time.perf_counter() > deadline:
                    raise RuntimeError("Cache panel benchmark timed out")

        def phase(kind, action):
            gaps = []
            current_gaps[0] = gaps
            invoked = []
            finished = []
            errors = []
            started = time.perf_counter()

            def invoke():
                begin = time.perf_counter()
                try:
                    action()
                except _CALLBACK_ERRORS as exc:
                    errors.append(exc)
                    finished.append(True)
                    return
                invoked.append((time.perf_counter() - begin) * 1000)
                poll_done()

            def poll_done():
                service = getattr(gui, "_cache_maintenance", None)
                if service is not None and service.busy:
                    root.after(5, poll_done)
                else:
                    root.after(50, lambda: finished.append(True))

            root.after(30, invoke)
            pump_until(lambda: finished)
            if errors:
                raise errors[0]
            current_gaps[0] = None
            ordered = sorted(gaps)
            return {"operation": kind, "callback_ms": invoked[0], "total_ms": (time.perf_counter() - started) * 1000,
                    "heartbeat_max_ms": max(gaps), "heartbeat_p95_ms": ordered[max(0, int(len(ordered) * .95))]}

        def button(parent, label):
            return next(child for child in descendants(parent)
                        if isinstance(child, ctk.CTkButton) and child.cget("text") == label)

        # Finish the main window's first layout before timing utility panels.
        warmed = []
        root.after(250, lambda: warmed.append(True))
        pump_until(lambda: warmed)
        heartbeat()
        try:
            for run in range(args.runs):
                for entry in ("inline", "dialog"):
                    owner = []

                    def open_panel(entry=entry, owner=owner):
                        if entry == "inline":
                            host = ctk.CTkToplevel(root)
                            host.withdraw()
                            frame = ctk.CTkFrame(host)
                            frame.pack()
                            owner.append(host)
                            gui._settings_inline_cache(frame, 0, gui._p(), True, ctk.StringVar(), host)
                        else:
                            panel_function(gui)
                            owner.append(gui._singleton_windows["cache_manager"])

                    samples = [phase("open", open_panel)]
                    samples.append(phase("save", button(owner[0], "Save limit" if entry == "inline" else "Save").invoke))
                    samples.append(phase("clear", button(owner[0], "Clear cache" if entry == "inline" else "Clear Cache").invoke))
                    if entry == "dialog":
                        samples.append(phase("refresh", button(owner[0], "Refresh").invoke))
                    results.append({"run": run, "entry": entry, "samples": samples})
                    owner[0].destroy()
                    root.update()
        finally:
            running[0] = False
            root.after_cancel(timer[0])
            gui._v46_finish_close()
    summary = {}
    for entry in ("inline", "dialog"):
        summary[entry] = {}
        for operation in ("open", "save", "clear", "refresh"):
            rows = [sample for row in results if row["entry"] == entry for sample in row["samples"]
                    if sample["operation"] == operation]
            if rows:
                summary[entry][operation] = {key: statistics.median(row[key] for row in rows)
                                            for key in ("callback_ms", "heartbeat_max_ms", "heartbeat_p95_ms")}
    report = {"runs": args.runs, "simulated_io_seconds": args.delay, "summary": summary, "samples": results,
              "note": "Real Tk scheduling, simulated 250 ms per cache read/maintenance call; no user cache touched."}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
