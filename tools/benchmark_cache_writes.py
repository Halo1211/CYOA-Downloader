"""Measure real concurrent cache puts using an isolated, disposable cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyoa_downloader_app.storage import cache


def run(entries: int, writes: int, workers: int, repeats: int) -> dict:
    samples = []
    with tempfile.TemporaryDirectory(prefix="cyoa-cache-write-benchmark-") as scratch:
        root = Path(scratch).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        index = {}
        for number in range(entries):
            body = f"seed:{number}:".encode().ljust(128, b"x")
            digest = hashlib.sha256(body).hexdigest()
            target = root / digest[:2] / digest
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(body)
            index[f"seed:{number}"] = digest
        with (
            patch.object(cache, "_CACHE_DIR", root),
            patch.object(cache, "_CACHE_IDX", root / "index.json"),
            patch.object(cache, "_cache_index", index),
            patch.object(cache, "_cache_dirty", {}),
            patch.object(cache, "_cache_removed", set()),
            patch.object(cache, "_cache_loaded", True),
            patch.object(cache, "_cache_limit_mb", lambda: 2048),
            patch.object(cache, "_v465_schedule_cache_save", lambda: None),
        ):
            original = cache._enforce_cache_limit
            for repetition in range(repeats):
                scans = 0
                count_lock = threading.Lock()

                def enforce(count_lock=count_lock):
                    nonlocal scans
                    with count_lock:
                        scans += 1
                    return original()

                def put(number, repetition=repetition):
                    url = f"write:{repetition}:{number}"
                    body = url.encode().ljust(512, b"y")
                    cache._cache_put(url, body)
                    return url, body

                with ThreadPoolExecutor(max_workers=workers) as pool, patch.object(cache, "_enforce_cache_limit", enforce):
                    started = time.perf_counter()
                    cpu = time.process_time()
                    results = list(pool.map(put, range(writes)))
                    elapsed = time.perf_counter() - started
                    cpu_elapsed = time.process_time() - cpu
                assert all(cache._cache_get(url) == body for url, body in results)
                samples.append({"seconds": elapsed, "cpu_seconds": cpu_elapsed, "budget_scans": scans, "verified_writes": len(results)})
    return {
        "seed_entries": entries, "writes_per_sample": writes, "workers": workers,
        "samples": samples,
        "median_seconds": statistics.median(sample["seconds"] for sample in samples),
        "median_cpu_seconds": statistics.median(sample["cpu_seconds"] for sample in samples),
        "median_budget_scans": statistics.median(sample["budget_scans"] for sample in samples),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=12000)
    parser.add_argument("--writes", type=int, default=48)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.entries, args.writes, args.workers, args.repeats)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
