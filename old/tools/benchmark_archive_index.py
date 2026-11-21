"""Compare trusted pre-change mirror scans with incremental metadata work."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.download import website
from tools.run_live_queue import CatalogueSnapshot


def scan_run(module, folder):
    reads = [0]
    original = Path.read_text
    def read(path, *args, **kwargs):
        reads[0] += 1
        return original(path, *args, **kwargs)
    with patch.object(module, "_load_settings", return_value={}):
        downloader = module.WebsiteDownloader("https://example.test/game/", str(folder), ai_mode="off")
    downloader._download_runtime_template_assets = lambda *_args: None
    try:
        with patch.object(Path, "read_text", read):
            started = time.perf_counter()
            for _ in range(6):
                downloader.localize_existing_text_assets()
            elapsed = time.perf_counter() - started
        return {"seconds": elapsed, "text_reads": reads[0]}
    finally:
        downloader.close()
        downloader.session.close()


def catalogue_run(folder, incremental):
    files = sorted(folder.glob("*.json"))
    snapshot = CatalogueSnapshot()
    reads = [0]
    original = Path.read_text
    def read(path, *args, **kwargs):
        reads[0] += 1
        return original(path, *args, **kwargs)
    results = None
    with patch.object(Path, "read_text", read):
        started = time.perf_counter()
        for _ in range(20):
            results = [snapshot.read(path, {}) if incremental else json.loads(path.read_text("utf-8")) for path in files]
        elapsed = time.perf_counter() - started
    return {"seconds": elapsed, "json_reads": reads[0]}, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-source", type=Path, required=True, help="Trusted pre-change website.py")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("cyoa_downloader_app.download._trusted_scan_control", args.control_source)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    results = {"runs": args.runs, "scan": {"before": [], "after": []}, "catalogue": {"before": [], "after": []}}
    with tempfile.TemporaryDirectory(prefix="cyoa-index-benchmark-") as temporary:
        root = Path(temporary)
        scripts, metadata = root / "scripts", root / "metadata"
        scripts.mkdir()
        metadata.mkdir()
        text = 'const description = "' + "ordinary words " * 2200 + '";\n'
        for number in range(120):
            (scripts / f"bundle{number}.js").write_text(text, "utf-8")
        expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in scripts.iterdir()}
        for number in range(600):
            (metadata / f"{number:04}.json").write_text(json.dumps({
                "id": number, "status": "downloaded", "title": f"Story {number}",
                "url": f"https://example.test/{number}/", "rows": list(range(400)),
            }), "utf-8")
        for run in range(args.runs):
            order = [("before", control), ("after", website)]
            if run % 2:
                order.reverse()
            for side, module in order:
                results["scan"][side].append(scan_run(module, scripts))
                measured, records = catalogue_run(metadata, side == "after")
                results["catalogue"][side].append(measured)
                assert len(records) == 600 and records[0]["id"] == 0
            actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in scripts.iterdir()}
            assert actual == expected
        results["verified_files"] = len(expected)
        results["verified_bytes"] = sum(path.stat().st_size for path in scripts.iterdir())
    for section in ("scan", "catalogue"):
        results[section]["median_seconds"] = {side: statistics.median(item["seconds"] for item in results[section][side])
                                              for side in ("before", "after")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), "utf-8")
    print(json.dumps({section: results[section]["median_seconds"] for section in ("scan", "catalogue")}, indent=2))


if __name__ == "__main__":
    main()
