"""Offline runtime benchmarks: python tools/benchmark_runtime.py.

Reports measurements rather than enforcing machine-dependent timing thresholds.
Uses temporary cache files and compares identical scheduler workloads.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


def benchmark_archive_script_scans() -> dict:
    """Compare identical unchanged scripts over a 40-page archive."""
    from cyoa_downloader_app.download.website import WebsiteDownloader

    measurements = {}
    with tempfile.TemporaryDirectory(prefix="cyoa-script-benchmark-") as scratch:
        files = []
        for number in range(80):
            path = Path(scratch) / f"app{number}.js"
            path.write_text("/* unchanged application script */\n" * 4000, encoding="utf-8")
            files.append(path)
        downloader = WebsiteDownloader.__new__(WebsiteDownloader)
        downloader._downloaded = {f"https://example.test/{path.name}": str(path) for path in files}
        downloader._browser_fetch_session = None

        def control():
            # Former behavior: read every script again for every HTML page.
            for path in files:
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                if "__cyoaLocalAudioPatch" in text or "function playVideo" not in text:
                    continue
                assert "youtube.com/embed" not in text

        original_read = Path.read_text
        for label, scan in (("control", control), ("optimized", downloader._patch_local_audio_scripts)):
            reads = 0

            def counted_read(path, *args, **kwargs):
                nonlocal reads
                reads += 1
                return original_read(path, *args, **kwargs)

            started = time.perf_counter()
            with patch.object(Path, "read_text", counted_read):
                for _ in range(40):
                    scan()
            measurements[f"archive_script_scan_{label}_seconds"] = time.perf_counter() - started
            measurements[f"archive_script_scan_{label}_file_reads"] = reads
    return measurements


def benchmark_css_processing() -> dict:
    """Measure CSS processing with and without redundant JS inference."""
    from cyoa_downloader_app.download.website import WebsiteDownloader

    css = "\n".join(f'.card{i} {{ color: red; --label: "A static label"; }}' for i in range(12000))
    instance = WebsiteDownloader.__new__(WebsiteDownloader)
    instance._browser_fetch_session = None
    rewrite = instance._rewrite_direct_urls

    def control(text, url, local, **_kwargs):
        return rewrite(text, url, local, css_context=False)

    measurements = {"css_fixture_bytes": len(css.encode("utf-8"))}
    for label, implementation in (("control", control), ("optimized", rewrite)):
        samples = []
        with patch.object(instance, "_rewrite_direct_urls", implementation):
            for _ in range(5):
                started = time.perf_counter()
                assert instance._process_css(css, "https://example.test/style.css", "style.css") == css
                samples.append(time.perf_counter() - started)
        measurements[f"css_processing_{label}_median_seconds"] = statistics.median(samples)
    return measurements


def benchmark_responsive_images() -> dict:
    """Measure parsing/localization overhead without network or disk writes."""
    from cyoa_downloader_app.download.website import WebsiteDownloader

    instance = WebsiteDownloader.__new__(WebsiteDownloader)
    instance._browser_fetch_session = None
    instance._download_asset = lambda *_args, **_kwargs: None
    measurements = {}
    for count in (1000, 4000):
        source = ", ".join(f"image{index}.png {index + 1}w" for index in range(count))
        samples = []
        for _ in range(5):
            started = time.perf_counter()
            assert not instance._set_attr_local(
                {"srcset": source}, "srcset", "https://example.test/", "index.html",
            )
            samples.append(time.perf_counter() - started)
        measurements[f"srcset_{count}_candidates_median_seconds"] = statistics.median(samples)
    return measurements


def benchmark_deep_scan_walks() -> dict:
    """Count filesystem work across two offline discovery rounds."""
    import requests

    from cyoa_downloader_app.download import image_pipeline

    walks = 0
    entries = 0
    original_walk = image_pipeline.os.walk

    def counted_walk(*args, **kwargs):
        nonlocal walks, entries
        walks += 1
        for root, directories, files in original_walk(*args, **kwargs):
            entries += len(files)
            yield root, directories, files

    def fetch(url, **_kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "image/png"
        response._content = b"PNG"
        response._content_consumed = True
        return response

    with tempfile.TemporaryDirectory(prefix="cyoa-deep-walk-benchmark-") as scratch:
        folder = Path(scratch)
        for index in range(2000):
            (folder / f"cached{index}.bin").write_bytes(b"x")
        (folder / "seed.css").write_text("body{background:url(hero.png)}", encoding="utf-8")
        with (
            patch.object(image_pipeline.os, "walk", counted_walk),
            patch.object(image_pipeline.runtime_state, "_HTTP2_ENABLED", False),
            patch.object(image_pipeline, "fetch_response", fetch),
            patch.object(image_pipeline, "vpn_requirement_satisfied", lambda: True),
            patch.object(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False),
            patch.object(image_pipeline, "_domain_throttle", lambda *_a: None),
            patch.object(image_pipeline, "_throttle_bandwidth", lambda *_a: None),
            patch.object(image_pipeline, "run_asset_scanner_plugins", image_pipeline._scan_file_for_assets),
        ):
            result = image_pipeline._deep_scan_and_download_assets(
                scratch, "https://example.test/", scratch, max_workers=1, ai_mode="off",
            )
            assert set(result) == {"https://example.test/hero.png"}
    return {"deep_scan_tree_walks": walks, "deep_scan_file_entries_seen": entries}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from cyoa_downloader_app.core.concurrency import iter_bounded_results
    from cyoa_downloader_app.storage import cache

    measurements = {}
    measurements.update(benchmark_archive_script_scans())
    measurements.update(benchmark_css_processing())
    measurements.update(benchmark_responsive_images())
    measurements.update(benchmark_deep_scan_walks())
    for option in ("--version", "--help"):
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            subprocess.run(
                [sys.executable, str(root / "cyoa_downloader.py"), option],
                capture_output=True, check=True, timeout=30,
            )
            samples.append(time.perf_counter() - started)
        measurements[option + "_median_seconds"] = statistics.median(samples)

    with tempfile.TemporaryDirectory(prefix="cyoa-cache-benchmark-") as scratch:
        cache_root = Path(scratch)
        for number in range(2000):
            digest = f"{number:064x}"
            bucket = cache_root / digest[:2]
            bucket.mkdir(exist_ok=True)
            (bucket / digest).write_bytes(b"x" * 64)
        with patch.object(cache, "_CACHE_DIR", cache_root), patch.object(cache, "_cache_limit_mb", lambda: 2048):
            samples = []
            for _ in range(5):
                started = time.perf_counter()
                assert cache._enforce_cache_limit() == 0
                samples.append(time.perf_counter() - started)
            measurements["cache_scan_2000_files_median_seconds"] = statistics.median(samples)

    for bounded in (False, True):
        tracemalloc.start()
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                if bounded:
                    checksum = sum(result for _, result in iter_bounded_results(
                        executor, int, range(10000), max_pending=8,
                    ))
                else:
                    futures = [executor.submit(int, number) for number in range(10000)]
                    checksum = sum(future.result() for future in futures)
                    del futures
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert checksum == 49995000
        label = "bounded" if bounded else "eager_control"
        measurements[f"scheduler_{label}_peak_mib"] = peak / (1024 * 1024)
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()
