"""Isolated real-Tk queue memory/latency fixture; never downloads user URLs."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sample(args):
    with tempfile.TemporaryDirectory(prefix="cyoa-queue-view-") as profile:
        os.environ["USERPROFILE"] = profile
        import customtkinter as ctk

        from cyoa_downloader_app.gui.queue_import import QueueImport, cancel_queue_import
        from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

        root = ctk.CTk()
        root.withdraw()
        root.attributes("-alpha", 0.0)
        gui = CYOADownloaderGUI(root)
        root.geometry("1280x900+20+20")
        root.deiconify()
        root.lower()
        process = psutil.Process()
        warmed = time.perf_counter() + .3
        while time.perf_counter() < warmed:
            root.update()
            time.sleep(.001)
        baseline = process.memory_info().rss
        peak = [baseline]
        gaps = []
        last = [time.perf_counter()]
        active = [True]
        after = [None]
        aborted = [False]
        started = time.perf_counter()

        def tick():
            now = time.perf_counter()
            gaps.append((now - last[0]) * 1000)
            last[0] = now
            peak[0] = max(peak[0], process.memory_info().rss)
            if (peak[0] - baseline > args.budget_mib * 1048576
                    or (getattr(gui, "_queue_import_busy", False) and now - started > args.time_limit)):
                aborted[0] = True
                cancel_queue_import(gui)
            if active[0]:
                after[0] = root.after(5, tick)

        rows = [{"url": f"https://example.invalid/story/{i}", "filename": f"Story {i}", "mode": "auto"}
                for i in range(args.items)]
        tick()
        started = time.perf_counter()
        try:
            QueueImport(gui, "synthetic fixture").accept(rows)
            def pending():
                view = getattr(gui, "_queue_view", None)
                return gui._queue_import_busy or (view is not None and view.after_id is not None)

            while pending():
                root.update()
                time.sleep(.001)
                if time.perf_counter() - started > 120:
                    aborted[0] = True
                    cancel_queue_import(gui)
            imported_seconds = time.perf_counter() - started
            settle = time.perf_counter() + .1
            while time.perf_counter() < settle:
                root.update()
                time.sleep(.001)
            if not aborted[0]:
                assert len(gui._queue_data) == args.items
                assert len({row["_queue_id"] for row in gui._queue_data}) == args.items
                canvas = gui._qlist._parent_canvas
                for position in (0.5, 1.0, 0.0):
                    canvas.yview_moveto(position)
                    until = time.perf_counter() + .08
                    while time.perf_counter() < until:
                        root.update()
                        time.sleep(.001)
            peak[0] = max(peak[0], process.memory_info().rss)
            result = {"requested_items": args.items, "imported_items": len(gui._queue_data),
                      "budget_aborted": aborted[0], "import_seconds": imported_seconds,
                      "baseline_rss_bytes": baseline, "peak_rss_bytes": peak[0], "rss_growth_bytes": peak[0] - baseline,
                      "row_widgets": len(gui._queue_rows), "heartbeat_max_ms": max(gaps),
                      "heartbeat_p95_ms": sorted(gaps)[int(.95 * (len(gaps) - 1))]}
        finally:
            active[0] = False
            root.after_cancel(after[0])
            gui._v46_finish_close()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=138)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--budget-mib", type=int, default=700)
    parser.add_argument("--time-limit", type=float, default=30)
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.sample:
        print(json.dumps(sample(args)))
        return
    values = []
    for _ in range(args.runs):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--sample", "--items", str(args.items),
                                 "--budget-mib", str(args.budget_mib), "--time-limit", str(args.time_limit)], capture_output=True, text=True, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        values.append(json.loads(result.stdout))
    report = {"items": args.items, "samples": values,
              "medians": {key: statistics.median(row[key] for row in values) for key in values[0]},
              "note": "Real Tk in transparent window, synthetic model import and scrolling; no network. RAM budget limits old widget growth."}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
