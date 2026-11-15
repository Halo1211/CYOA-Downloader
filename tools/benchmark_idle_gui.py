"""Compare quiet/active mapped Tk views with a trusted source checkout."""

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

ROOT = Path(__file__).resolve().parents[1]


def worker(args):
    sys.path.insert(0, str(args.source_root))
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    counts = {"tk_calls": 0, "progress_draws": 0}
    class TkBridge:
        def __init__(self, native):
            self.native = native
        def call(self, *values):
            counts["tk_calls"] += 1
            return self.native.call(*values)
        def __getattr__(self, name):
            return getattr(self.native, name)
    native_draw = ctk.CTkProgressBar._draw
    def draw(self, *values, **options):
        counts["progress_draws"] += 1
        return native_draw(self, *values, **options)
    ctk.CTkProgressBar._draw = draw
    root = ctk.CTk()
    root.attributes("-alpha", 0)
    root.tk = TkBridge(root.tk)
    gui = CYOADownloaderGUI(root)
    root.geometry("1100x720")
    root.lower()
    root.update_idletasks()
    results, delays = {}, []
    active, baseline = [None], [{}]
    scenarios = ("idle_collapsed", "idle_expanded", "active_collapsed", "active_expanded", "unmapped_active", "completed")
    def heartbeat(expected):
        if active[0]:
            delays.append(max(0, (time.perf_counter() - expected) * 1000))
        root.after(10, heartbeat, time.perf_counter() + .01)
    def render():
        if gui._is_running:
            gui._v46_render_progress(gui._v46_telemetry.snapshot())
        root.after(150, render)
    def begin(index):
        name = scenarios[index]
        active[0] = None
        if name == "idle_expanded":
            gui._v46_apply_progress_visibility(True)
        elif name == "active_collapsed":
            gui._is_running = True
            gui._v46_telemetry.apply({"type": "job_started", "source_url": "https://fixture.invalid/"})
            gui._v46_telemetry.apply({"type": "file_started", "name": "fixture.bin"})
            gui._v46_apply_progress_visibility(False)
        elif name == "active_expanded":
            gui._v46_apply_progress_visibility(True)
        elif name == "unmapped_active":
            root.withdraw()
        elif name == "completed":
            gui._is_running = False
            gui._v46_telemetry.apply({"type": "job_completed"})
            root.deiconify()
            gui._v46_render_progress(gui._v46_telemetry.snapshot())
        def settled():
            delays.clear()
            baseline[0] = dict(counts, cpu=time.process_time(), wall=time.perf_counter())
            active[0] = name
            root.after(int(args.seconds * 1000), lambda: finish(index))
        root.after(250, settled)
    def finish(index):
        elapsed = time.perf_counter() - baseline[0]["wall"]
        ordered = sorted(delays)
        results[scenarios[index]] = {
            "seconds": elapsed, "cpu_seconds": time.process_time() - baseline[0]["cpu"],
            "tk_calls": counts["tk_calls"] - baseline[0]["tk_calls"],
            "progress_draws": counts["progress_draws"] - baseline[0]["progress_draws"],
            "heartbeat_p95_ms": ordered[min(len(ordered)-1, int(len(ordered)*.95))],
            "heartbeat_max_ms": max(delays),
        }
        active[0] = None
        if index + 1 < len(scenarios):
            begin(index + 1)
        else:
            root.quit()
    root.after(10, heartbeat, time.perf_counter() + .01)
    root.after(150, render)
    root.after(1000, lambda: begin(0))
    try:
        root.mainloop()
    finally:
        gui._is_running = False
        gui._v46_finish_close()
    args.output.write_text(json.dumps(results, indent=2), "utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    sources = [("after", ROOT)]
    if args.control_root:
        sources.insert(0, ("before", args.control_root.resolve(strict=True)))
    samples = {side: [] for side, _source in sources}
    with tempfile.TemporaryDirectory(prefix="cyoa-idle-benchmark-") as temporary:
        for run_index in range(args.runs):
            order = sources if run_index % 2 == 0 else list(reversed(sources))
            for side, source in order:
                profile = Path(temporary) / f"{side}-{run_index}"
                profile.mkdir()
                report = profile / "worker.json"
                environment = dict(os.environ, USERPROFILE=str(profile), PYTHONIOENCODING="utf-8")
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--source-root", str(source),
                                         "--seconds", str(args.seconds), "--output", str(report)],
                                        env=environment, cwd=profile, capture_output=True, timeout=120, check=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if result.returncode:
                    raise RuntimeError(result.stderr[-3000:].decode("utf-8", "replace"))
                diagnostics = result.stderr.decode("utf-8", "replace")
                assert "Exception in Tkinter callback" not in diagnostics and "invalid command name" not in diagnostics, diagnostics[-2000:]
                samples[side].append(json.loads(report.read_text("utf-8")))
    summary = {}
    for side, rows in samples.items():
        summary[side] = {name: {metric: statistics.median(row[name][metric] for row in rows) for metric in rows[0][name]}
                         for name in rows[0]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"runs": args.runs, "samples": samples, "median": summary,
                                      "window": "Mapped transparent real Tk, 1100x720, fresh profile/process, no downloads",
                                      "instrumentation": "Counts Tk bridge calls and progress canvas draws; not OS compositor latency"}, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
