"""Paired isolated queue-start/Tk and cache-allocation fixtures; no downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def allocation(work):
    tracemalloc.start()
    begin = time.perf_counter()
    try:
        work()
        return {"seconds": time.perf_counter() - begin, "peak_python_mib": tracemalloc.get_traced_memory()[1] / 1048576}
    finally:
        tracemalloc.stop()


def queue_worker(args):
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI
    from cyoa_downloader_app.storage import resume

    root = ctk.CTk()
    root.attributes("-alpha", 0)
    gui = CYOADownloaderGUI(root)
    root.geometry("1280x900")
    gui._queue_data.extend(
        {"url": f"https://fixture.invalid/story/{i}", "filename": "" if i % 2 else f"Story {i} 雪",
         "mode": "" if i % 3 else "embed", "_queue_id": str(i)}
        for i in range(args.items)
    )
    gui._queue_view.changed()
    gui._outdir_var.set(str(Path.cwd() / "output"))
    gui._prepare_ytdlp_cookies = lambda: True
    gui._show_results = lambda: None
    ui_thread = threading.get_ident()
    calls = {"ui": 0, "worker": 0}
    native_key = resume.resume_job_key
    native_worker = gui._worker
    native_sink = gui._v46_set_event_sink
    rows, gaps, result, errors = [], [], {}, []
    active, begin = [False], [0]

    def key(*values):
        calls["ui" if threading.get_ident() == ui_thread else "worker"] += 1
        return native_key(*values)

    def worker(*values):
        rows.append(values[0])
        return native_worker(*values)

    def sink():
        if threading.get_ident() == ui_thread:
            native_sink()
        else:
            result["identities_ready_ms"] = (time.perf_counter() - begin[0]) * 1000
            raise OSError("fixture: stop before any transport setup")

    def done():
        gui._is_running = False
        root.after(150, root.quit)

    def beat(expected):
        if active[0]:
            gaps.append(max(0, (time.perf_counter() - expected) * 1000))
        root.after(10, beat, time.perf_counter() + .01)

    def invoke():
        active[0] = True
        begin[0] = time.perf_counter()
        gui._start()
        result["start_callback_ms"] = (time.perf_counter() - begin[0]) * 1000

    gui._worker, gui._v46_set_event_sink, gui._done = worker, sink, done
    root.report_callback_exception = lambda *exc: (errors.append(str(exc[1])), root.quit())
    root.after(10, beat, time.perf_counter() + .01)
    root.after(500, invoke)
    watchdog = root.after(45000, root.quit)
    try:
        with patch.object(resume, "resume_job_key", key):
            root.mainloop()
        assert not errors and "identities_ready_ms" in result, errors
        result.update(
            heartbeat_p95_ms=sorted(gaps)[int(.95 * (len(gaps) - 1))],
            heartbeat_max_ms=max(gaps),
            identity_hashes_on_ui=calls["ui"], identity_hashes_on_worker=calls["worker"],
            jobs=len(rows[0]),
            identities_sha256=hashlib.sha256("\n".join(row["_resume_key"] for row in rows[0]).encode("utf-8")).hexdigest(),
        )
        assert len(rows[0]) == args.items and calls["ui"] + calls["worker"] == args.items
        assert all("_run_file_name" not in item for item in gui._queue_data)
    finally:
        root.after_cancel(watchdog)
        gui._v46_finish_close()
    return result


def cache_worker(args):
    from cyoa_downloader_app.storage import cache

    profile = Path.cwd()
    with ExitStack() as stack:
        for name, value in (
            ("_cache_index", {}), ("_cache_dirty", {}), ("_cache_removed", set()), ("_cache_loaded", True),
            ("_cache_write_generation", 0), ("_cache_replace_generation", 0), ("_cache_flushed_replace_generation", 0),
            ("_v465_schedule_cache_save", lambda: None), ("_enforce_cache_after_write", lambda _generation: None),
        ):
            stack.enter_context(patch.object(cache, name, value))
        data = b"asset123" * (args.cache_mib * 1048576 // 8)
        digest = hashlib.sha256(data).hexdigest()
        directory = profile / "verification"
        target = directory / digest[:2] / digest
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        stack.enter_context(patch.object(cache, "_CACHE_DIR", directory))
        stack.enter_context(patch.object(cache, "_CACHE_IDX", directory / "index.json"))
        verification = allocation(lambda: cache._cache_put("alias", data))
        assert cache._cache_index["alias"] == digest
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest

        directory = profile / "index"
        cache._CACHE_DIR, cache._CACHE_IDX = directory, directory / "index.json"
        cache._cache_index = {f"url:{i}": "a" * 64 for i in range(args.entries)}
        cache._cache_dirty, cache._cache_removed = {}, set()
        clean = allocation(cache._v465_flush_cache_index)
        clean["directory_created"] = directory.exists()
        directory.mkdir(exist_ok=True)
        cache._CACHE_IDX.write_text(json.dumps({"external": "b" * 64}), encoding="utf-8")
        cache._cache_index["new"] = cache._cache_dirty["new"] = "c" * 64
        delta = allocation(cache._v465_flush_cache_index)
        assert json.loads(cache._CACHE_IDX.read_text("utf-8")) == {"external": "b" * 64, "new": "c" * 64}
    return {"validation": verification, "clean_flush": clean, "delta_flush": delta}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--items", type=int, default=50000)
    parser.add_argument("--cache-mib", type=int, default=32)
    parser.add_argument("--entries", type=int, default=150000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=("queue", "cache"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.worker:
        sys.path.insert(0, str(args.source_root.resolve()))
        logging.disable(logging.CRITICAL)
        result = (queue_worker if args.worker == "queue" else cache_worker)(args)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return
    sources = [("after", ROOT)]
    if args.control_root:
        sources.insert(0, ("before", args.control_root.resolve(strict=True)))
    samples = {kind: {side: [] for side, _ in sources} for kind in ("queue", "cache")}
    with tempfile.TemporaryDirectory(prefix="cyoa-queue-cache-") as scratch:
        for kind in samples:
            for run in range(args.runs):
                for side, source in sources if run % 2 == 0 else reversed(sources):
                    profile = Path(scratch) / f"{kind}-{side}-{run}"
                    profile.mkdir()
                    report = profile / "worker.json"
                    process = subprocess.run([
                        sys.executable, str(Path(__file__).resolve()), "--worker", kind,
                        "--source-root", str(source), "--items", str(args.items), "--cache-mib", str(args.cache_mib),
                        "--entries", str(args.entries), "--output", str(report),
                    ], cwd=profile, env=dict(os.environ, USERPROFILE=str(profile), PYTHONIOENCODING="utf-8"),
                        capture_output=True, timeout=90, check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    error = process.stderr.decode("utf-8", "replace")
                    assert process.returncode == 0 and "invalid command name" not in error and "Exception in Tkinter callback" not in error, error[-4000:]
                    samples[kind][side].append(json.loads(report.read_text("utf-8")))
    identity_hashes = {row["identities_sha256"] for rows in samples["queue"].values() for row in rows}
    assert len(identity_hashes) == 1, "run identities changed across implementations"

    def median(rows):
        result = {}
        for key, value in rows[0].items():
            if isinstance(value, dict):
                result[key] = median([row[key] for row in rows])
            elif isinstance(value, (int, float)):
                result[key] = statistics.median(row[key] for row in rows)
            else:
                result[key] = value
        return result
    summary = {kind: {side: median(rows) for side, rows in sides.items()} for kind, sides in samples.items()}
    result = {"runs": args.runs, "items": args.items, "cache_mib": args.cache_mib, "index_entries": args.entries,
              "samples": samples, "median": summary,
              "limits": "Synthetic isolated profiles; real mapped transparent Tk; no downloads. Cache input bytes/index creation excluded from tracemalloc; quota enforcement/index persistence excluded from payload validation. Allocation is not total RSS; warm local files and Tk scheduling are not OS-cold/physical-input performance."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
