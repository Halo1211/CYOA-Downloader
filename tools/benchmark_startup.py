"""Measure actual mapped Tk windows and delivered events in isolated processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def run(command, root, label, timeout):
    profile = root / uuid.uuid4().hex
    profile.mkdir()
    report, log = profile / "startup.json", profile / "process.log"
    token = uuid.uuid4().hex
    environment = dict(os.environ, USERPROFILE=str(profile), APPDATA=str(profile / "roaming"),
                       LOCALAPPDATA=str(profile / "local"), CYOA_STARTUP_REPORT=str(report),
                       CYOA_STARTUP_TOKEN=token, CYOA_STARTUP_EXIT="1")
    # perf_counter has a system-wide origin on supported Python/Windows.
    started = time.perf_counter_ns()
    environment["CYOA_STARTUP_STARTED_NS"] = str(started)
    peak, owned = 0, {}
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=profile, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        parent = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                rss = 0
                try:
                    members = [parent, *parent.children(recursive=True)]
                except psutil.Error:
                    members = []
                for member in members:
                    try:
                        owned[member.pid] = member
                        rss += member.memory_info().rss
                    except psutil.Error:
                        pass
                peak = max(peak, rss)
                if (time.perf_counter_ns() - started) / 1e9 > timeout:
                    raise TimeoutError(f"Startup probe timed out ({label})")
                time.sleep(0.02)
            if process.returncode:
                raise RuntimeError(f"Startup probe exited {process.returncode}: {log.read_text('utf-8')[-1500:]}")
            diagnostics = log.read_text("utf-8", errors="replace")
            assert "Exception in Tkinter callback" not in diagnostics and "invalid command name" not in diagnostics, diagnostics[-2000:]
            result = json.loads(report.read_text("utf-8"))
            assert result["token"] == token
            assert {"entry", "gui_composed", "root_created", "window_mapped", "ui_constructed", "input_event_ready"} <= result["phases_ns"].keys()
            assert result["phases_ns"]["input_event_ready"] >= max(result["phases_ns"]["window_mapped"], result["phases_ns"]["ui_constructed"])
            result.update(label=label, peak_process_tree_rss_mib=peak / 1024 ** 2)
            return result
        finally:
            # Only descendants observed beneath this fixture's own process.
            for member in reversed(list(owned.values())):
                try:
                    if member.is_running():
                        member.kill()
                except psutil.Error:
                    pass
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--source", action="store_true")
    parser.add_argument("--runs", type=int, default=6, help="First observed pair plus at least five repeated pairs")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commands = {}
    if args.source:
        commands["source"] = [sys.executable, str(ROOT / "cyoa_downloader.py"), "--gui"]
    for side in ("before", "after"):
        executable = getattr(args, side)
        if executable:
            commands[side] = [str(executable.resolve(strict=True)), "--gui"]
    if not commands:
        parser.error("Choose --source and/or --before/--after instrumented executables")
    samples = {side: [] for side in commands}
    with tempfile.TemporaryDirectory(prefix="cyoa-startup-") as temporary:
        for index in range(args.runs):
            order = list(commands.items())
            if index % 2:
                order.reverse()
            for side, command in order:
                samples[side].append(run(command, Path(temporary), f"{side}-{index}", args.timeout))
    summary = {}
    for side, rows in samples.items():
        repeated = rows[1:] or rows
        summary[side] = {"first_observed_seconds": rows[0]["seconds"],
                         "repeated_median_seconds": {name: statistics.median(row["seconds"][name] for row in repeated)
                                                     for name in repeated[0]["seconds"]},
                         "repeated_peak_rss_mib_median": statistics.median(row["peak_process_tree_rss_mib"] for row in repeated)}
    result = {"runs_per_side": args.runs, "samples": samples, "summary": summary,
              "cache_state": "First observed launch and repeated launches; OS cache is uncontrolled, not a proven OS-cold test",
              "profile": "Fresh isolated profile and process per launch; no user data or remote downloads",
              "rss_sampling_ms": 20}
    result["executable_sha256"] = {}
    for side, command in commands.items():
        if side != "source":
            with open(command[0], "rb") as stream:
                digest = hashlib.sha256()
                while data := stream.read(1024 * 1024):
                    digest.update(data)
            result["executable_sha256"][side] = digest.hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
