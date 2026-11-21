"""Measure real Tk event-loop delay under offline log/progress traffic.

An isolated profile and transparent mapped window leave the user's settings
and downloads alone. Timings measure Tk scheduling, not OS mouse/compositor
latency. Run with --output report.json; optional limits make a red/green probe.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import math
import os
import re
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch


def distribution(values):
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.50) - 1)], 3) if ordered else 0,
        "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 3) if ordered else 0,
        "max_ms": round(max(ordered, default=0), 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assert-max-ms", type=float, default=0)
    parser.add_argument("--assert-log-p95-ms", type=float, default=0)
    parser.add_argument("--log-control", type=Path, help="Trusted source snapshot for a before/after comparison")
    parser.add_argument("--progress-control", type=Path, help="Trusted progress-poll function snapshot")
    parser.add_argument("--assert-progress-all", action="store_true")
    parser.add_argument("--assert-queue-font-growth", type=int, default=0)
    parser.add_argument(
        "--queue-size", type=int, default=0, help="Also measure importing this many synthetic offline rows"
    )
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="cyoa-gui-latency-") as profile:
        os.environ["USERPROFILE"] = profile
        os.environ["HOME"] = profile
        imported = time.perf_counter()
        import customtkinter as ctk

        from cyoa_downloader_app.gui import final_behaviors
        from cyoa_downloader_app.gui.logging_ui import GUILogHandler
        from cyoa_downloader_app.logging_setup import _formatter
        from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

        import_seconds = time.perf_counter() - imported
        started = time.perf_counter()
        root = ctk.CTk()
        root.withdraw()
        root.attributes("-alpha", 0.0)
        gui = CYOADownloaderGUI(root)
        if args.log_control:
            spec = importlib.util.spec_from_file_location("cyoa_downloader_app.gui._latency_control", args.log_control)
            control = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(control)
            gui._poll_log = types.MethodType(control._v465_poll_log, gui)
        if args.progress_control:
            namespace = dict(vars(final_behaviors))
            exec(compile(args.progress_control.read_text("utf-8"), str(args.progress_control), "exec"), namespace)
            gui._v46_poll_progress = types.MethodType(namespace["_v46_poll_progress"], gui)
        root.geometry("1280x900+20+20")
        root.deiconify()
        root.lower()
        root.update_idletasks()
        construction_seconds = time.perf_counter() - started
        handler = GUILogHandler(gui._log_queue)
        handler.setFormatter(_formatter)
        pending_logs = {}
        log_latency = []
        native_text = getattr(gui._log_txt, "_textbox", gui._log_txt)
        native_insert = native_text.insert
        insert_calls = [0]

        def measured_insert(index, *values):
            result = native_insert(index, *values)
            insert_calls[0] += 1
            now = time.perf_counter()
            for value in values:
                if isinstance(value, str):
                    for match in re.finditer(r"Saved asset image_(\d+)\.webp", value):
                        created = pending_logs.pop(int(match.group(1)), None)
                        if created is not None:
                            log_latency.append((now - created) * 1000)
            return result

        native_text.insert = measured_insert
        callback_times = {}
        for name in ("_poll_log", "_v46_poll_progress"):
            original = getattr(gui, name)
            samples = callback_times.setdefault(name, [])

            def measured(*values, method=original, measurements=samples):
                begin = time.perf_counter()
                try:
                    return method(*values)
                finally:
                    measurements.append((time.perf_counter() - begin) * 1000)

            setattr(gui, name, measured)

        phases = [("idle", 2, 0), ("normal", 3, 200), ("burst", 3, 5000), ("drain", 6, 0)]
        queue_source = Path(profile) / "queue.csv"
        if args.queue_size:
            with queue_source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["url", "filename", "mode"])
                writer.writerows(
                    (f"https://example.invalid/story/{i}", f"Benchmark {i}", "website_folder")
                    for i in range(args.queue_size)
                )
            phases.insert(0, ("queue_import", 1, 0))
        heartbeat = {name: [] for name, _, _ in phases}
        active = [None]
        stop = threading.Event()
        producer = [None]
        produced = {"logs": 0, "progress": 0}
        phase_reports = {}

        def produce(rate):
            batch = max(1, rate // 100)
            deadline = time.perf_counter()
            while not stop.is_set():
                for _ in range(batch):
                    produced["logs"] += 1
                    count = produced["logs"]
                    pending_logs[count] = time.perf_counter()
                    record = logging.LogRecord(
                        "gui-benchmark", logging.INFO, "", 0, f"Saved asset image_{count}.webp", (), None
                    )
                    handler.emit(record)
                    gui._v46_enqueue_progress({"type": "file_completed", "name": "fixture.webp"})
                    produced["progress"] += 1
                    if count % 10 == 0:
                        gui._set_status(f"Offline benchmark asset {count}")
                deadline += 0.01
                stop.wait(max(0, deadline - time.perf_counter()))

        def tick(expected):
            now = time.perf_counter()
            if active[0] is not None:
                heartbeat[active[0]].append(max(0, (now - expected) * 1000))
            root.after(10, tick, time.perf_counter() + 0.01)

        def phase(index=0):
            if producer[0] is not None:
                stop.set()
                producer[0].join(1)
                stop.clear()
                producer[0] = None
            if active[0] is not None:
                phase_reports[active[0]]["log_backlog_end"] = gui._log_queue.qsize()
                phase_reports[active[0]]["cpu_seconds"] = round(
                    time.process_time() - phase_reports[active[0]].pop("cpu_started"), 3
                )
            if index == len(phases):
                root.quit()
                return
            name, seconds, rate = phases[index]
            active[0] = name
            gui._is_running = rate > 0
            phase_reports[name] = {"requested_records_per_second": rate, "cpu_started": time.process_time()}
            if name == "queue_import":
                begin_import = time.perf_counter()
                fonts_before = set(root.tk.call("font", "names"))
                with patch("tkinter.filedialog.askopenfilename", return_value=str(queue_source)):
                    gui._import_list()
                phase_reports[name]["import_call_ms"] = round((time.perf_counter() - begin_import) * 1000, 3)

                def import_ready():
                    if getattr(gui, "_queue_import_busy", False):
                        root.after(10, import_ready)
                        return
                    phase_reports[name]["queue_rows"] = len(gui._queue_data)
                    phase_reports[name]["tk_fonts_added"] = len(set(root.tk.call("font", "names")) - fonts_before)
                    phase_reports[name]["import_total_ms"] = round((time.perf_counter() - begin_import) * 1000, 3)
                    root.after(250, phase, index + 1)

                root.after(0, import_ready)
                return
            if rate:
                producer[0] = threading.Thread(target=produce, args=(rate,), daemon=True)
                producer[0].start()
            root.after(seconds * 1000, phase, index + 1)

        root.after(10, tick, time.perf_counter() + 0.01)
        root.after(1000, phase)
        cpu_started = time.process_time()
        wall_started = time.perf_counter()
        try:
            root.mainloop()
            report = {
                "import_seconds": round(import_seconds, 3),
                "construction_seconds": round(construction_seconds, 3),
                "phases": {
                    name: dict(phase_reports[name], **distribution(values)) for name, values in heartbeat.items()
                },
                "callbacks": {name: distribution(values) for name, values in callback_times.items()},
                "produced": produced,
                "progress_records_applied": gui._v46_telemetry.assets_finished,
                "progress_records_pending": gui._v46_progress_queue.qsize(),
                "log_presentation": distribution(log_latency),
                "native_text_insert_calls": insert_calls[0],
                "process_cpu_seconds": round(time.process_time() - cpu_started, 3),
                "measurement_seconds": round(time.perf_counter() - wall_started, 3),
                "window": "mapped, transparent; isolated profile; no network downloads",
            }
            try:
                import psutil

                memory = psutil.Process().memory_info()
                report["peak_rss_mib"] = round(getattr(memory, "peak_wset", memory.rss) / 1048576, 2)
            except ImportError:
                pass
        finally:
            stop.set()
            if producer[0] is not None:
                producer[0].join(1)
            gui._is_running = False
            gui._v46_finish_close()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if args.assert_max_ms:
            assert max(p["max_ms"] for p in report["phases"].values()) <= args.assert_max_ms, report["phases"]
        if args.assert_log_p95_ms:
            assert report["log_presentation"]["p95_ms"] <= args.assert_log_p95_ms, report["log_presentation"]
        if args.assert_progress_all:
            assert report["progress_records_applied"] == report["produced"]["progress"], report
        if args.assert_queue_font_growth:
            assert report["phases"]["queue_import"]["tk_fonts_added"] <= args.assert_queue_font_growth, report


if __name__ == "__main__":
    main()
