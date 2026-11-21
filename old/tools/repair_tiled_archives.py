"""Backfill tiled image-overlay pages in completed archives, preserving other files."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyoa_downloader_app.download.asset_scan import _tiled_overlay_asset_paths
from cyoa_downloader_app.download.website import WebsiteDownloader


def repair(root, item):
    folder = (root / item["entry"]).parent
    source = folder / "cyoa.js"
    if not source.is_file():
        return
    paths = _tiled_overlay_asset_paths(source.read_text(encoding="utf-8"))
    if not paths:
        return
    missing = sorted(path for path in paths if not (folder / path).is_file())
    downloader = WebsiteDownloader(item["url"], str(folder), max_workers=4, ai_mode="off")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda path: downloader.download_asset(urljoin(item["url"], path), preferred_kind="images"), missing
                )
            )
    finally:
        downloader.close()
        downloader.session.close()
    failed = sorted(path for path in paths if not (folder / path).is_file())
    report = {"expected": len(paths), "recovered": len(missing) - len(failed), "missing": failed}
    (folder / "tiled_page_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(item["id"], json.dumps(report), flush=True)


if __name__ == "__main__":
    # Compose the compatibility facade once before launching worker threads.
    import cyoa_downloader_app.runtime.surface  # noqa: F401

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    items = json.loads((root / "_audit/queue_status.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(partial(repair, root), [item for item in items if item["status"] == "downloaded"]))
