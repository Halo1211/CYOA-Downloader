"""Paired isolated report, inference and deep-scan measurements; no downloads."""

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
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def report_worker(args):
    import customtkinter as ctk

    from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

    root = ctk.CTk()
    root.attributes("-alpha", 0)
    gui = CYOADownloaderGUI(root)
    gui._language = "en"
    gui._last_results = [
        {"status": "OK" if i % 3 else "FAIL", "url": f"https://fixture.invalid/{i}",
         "filename": f"Story {i:05} 雪", "mode": "website_folder", "error": "fixture" if not i % 3 else ""}
        for i in range(args.records)
    ]
    errors, gaps, result = [], [], {}
    active, phase_started = [False], [0]
    root.report_callback_exception = lambda *exc: (errors.append(str(exc[1])), root.quit())

    def beat(expected):
        if active[0]:
            gaps.append(max(0, (time.perf_counter() - expected) * 1000))
        root.after(10, beat, time.perf_counter() + .01)

    def phase(name, action, next_phase):
        gaps.clear()
        active[0] = True
        phase_started[0] = time.perf_counter()
        begin = time.perf_counter()
        action()
        result[name + "_callback_ms"] = (time.perf_counter() - begin) * 1000

        def ready():
            if errors:
                root.quit()
                return
            win = gui._singleton_windows["reports_center"]
            pager, task = getattr(win, "_panel_pager", None), getattr(win, "_panel_task", None)
            if (pager is not None and not pager.ready) or (task is not None and task.busy):
                root.after(5, ready)
                return
            result[name + "_ready_ms"] = (time.perf_counter() - phase_started[0]) * 1000
            result[name + "_cards"] = len(pager.visible) if pager is not None else (
                args.records if name == "open" else sum(i % 3 == 0 for i in range(args.records)))
            root.after(150, finish)

        def finish():
            ordered = sorted(gaps)
            result[name + "_heartbeat_p95_ms"] = ordered[int(.95 * (len(ordered) - 1))]
            result[name + "_heartbeat_max_ms"] = max(ordered)
            active[0] = False
            next_phase()
        root.after(1, ready)

    def filtered():
        win = gui._singleton_windows["reports_center"]
        def descendants(owner):
            for child in owner.winfo_children():
                yield child
                yield from descendants(child)
        button = next(w for w in descendants(win) if isinstance(w, ctk.CTkButton) and w.cget("text") == "CYOA Failed")
        phase("filter", button.invoke, root.quit)

    def opened():
        before = set(root.tk.call("font", "names"))
        phase("open", gui._show_results, filtered)
        result["fonts_after_callback"] = len(set(root.tk.call("font", "names")) - before)

    root.after(10, beat, time.perf_counter() + .01)
    root.after(500, opened)
    watchdog = root.after(45000, root.quit)
    try:
        # Real mapped transparent windows, with no global modal input grab.
        with patch.object(ctk.CTkToplevel, "grab_set", lambda _self: None):
            original = ctk.CTkToplevel.__init__
            def top(widget, *values, **options):
                original(widget, *values, **options)
                widget.attributes("-alpha", 0)
            with patch.object(ctk.CTkToplevel, "__init__", top):
                root.mainloop()
    finally:
        root.after_cancel(watchdog)
        gui._v46_finish_close()
    assert not errors and "filter_ready_ms" in result, errors
    return result


def scan_worker(args):
    from cyoa_downloader_app.download import image_pipeline

    urls = {f"https://fixture.invalid/game/image{i}.png" for i in range(30)}
    folder = Path(args.fixture)
    parse_count = [0]
    native = image_pipeline.urlparse
    def parse(url, *values, **options):
        if url in urls:
            parse_count[0] += 1
        return native(url, *values, **options)
    with patch.object(image_pipeline, "run_asset_scanner_plugins", lambda *_a: set(urls)), \
         patch.object(image_pipeline, "urlparse", parse), \
         patch.object(image_pipeline, "fetch_response", side_effect=AssertionError("Unexpected download")):
        tracemalloc.start()
        begin = time.perf_counter()
        result = image_pipeline._deep_scan_and_download_assets(
            str(folder), "https://fixture.invalid/game/", str(folder), skip_urls=urls, max_workers=1, ai_mode="off")
        elapsed = time.perf_counter() - begin
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert result == {}
    return {"seconds": elapsed, "peak_python_mib": peak / 1048576, "canonical_parse_calls": parse_count[0]}


def inference_worker(args):
    from cyoa_downloader_app.download.asset_scan import _infer_generated_entry_images

    sources = {
        "contract": "'li_' + group + '_' + name.toLowerCase().replaceAll(' ', ''); 'img/" + "$" + "{entry.id}.webp';",
        "padding": "// ordinary padding\n" * 200000,
    }
    expected = set()
    for i in range(80):
        sources[f"class{i}"] = f"class Thing{i} extends Entry {{\nsuper(name, 'g{i}');\n}}\nnew Thing{i}('A/B {i}');"
        expected.add(f"img/li_g{i}_ab{i}.webp")
    tracemalloc.start()
    begin = time.perf_counter()
    result = _infer_generated_entry_images(sources)
    elapsed = time.perf_counter() - begin
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result == expected
    return {"seconds": elapsed, "peak_python_mib": peak / 1048576, "exact_paths": len(result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=("report", "scan", "inference"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    if args.worker:
        sys.path.insert(0, str(args.source_root.resolve()))
        logging.disable(logging.CRITICAL)
        result = {"report": report_worker, "scan": scan_worker, "inference": inference_worker}[args.worker](args)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return
    sources = [("after", ROOT)]
    if args.control_root:
        sources.insert(0, ("before", args.control_root.resolve(strict=True)))
    samples = {kind: {side: [] for side, _ in sources} for kind in ("report", "scan", "inference")}
    with tempfile.TemporaryDirectory(prefix="cyoa-bulk-benchmark-") as temporary:
        fixture = Path(temporary) / "scan"
        fixture.mkdir()
        data = ("// ordinary words no assets\n" * 40000).encode()
        for i in range(24):
            (fixture / f"bundle{i}.js").write_bytes(data)
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.iterdir()}
        for kind in samples:
            for run in range(args.runs):
                for side, source in sources if run % 2 == 0 else reversed(sources):
                    profile = Path(temporary) / f"{kind}-{side}-{run}"
                    profile.mkdir()
                    report = profile / "worker.json"
                    process = subprocess.run([
                        sys.executable, str(Path(__file__).resolve()), "--worker", kind,
                        "--source-root", str(source), "--fixture", str(fixture),
                        "--records", str(args.records), "--output", str(report),
                    ], cwd=profile, env=dict(os.environ, USERPROFILE=str(profile), PYTHONIOENCODING="utf-8"),
                        capture_output=True, timeout=90, check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    errors = process.stderr.decode("utf-8", "replace")
                    assert process.returncode == 0 and "invalid command name" not in errors and "Exception in Tkinter callback" not in errors, errors[-4000:]
                    samples[kind][side].append(json.loads(report.read_text("utf-8")))
        assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.iterdir()}
    medians = {kind: {side: {metric: statistics.median(row[metric] for row in rows) for metric in rows[0]}
                      for side, rows in sides.items()} for kind, sides in samples.items()}
    result = {"runs": args.runs, "records": args.records, "median": medians, "samples": samples,
              "scan_fixture_bytes": len(data) * 24, "verified_unchanged_files": 24,
              "limits": "Synthetic local fixtures; scan plugin discovery excluded to isolate inference/canonicalization; Python allocation peak is not total RSS; Tk scheduling is not physical input/compositor latency."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(medians, indent=2))


if __name__ == "__main__":
    main()
