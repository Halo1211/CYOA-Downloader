"""Repair archived project CSS and older Plus 2 runtime defaults in place."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cyoa_downloader_app.runtime.surface  # noqa: F401
from cyoa_downloader_app.core.atomic_io import atomic_write_text
from cyoa_downloader_app.download.asset_scan import _scan_file_for_assets
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.integrations.offline_viewers.modernizer import _inject_marked_script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--id", action="append", default=[])
    args = parser.parse_args()
    root = args.output.resolve()
    items = json.loads((root / "_audit/queue_status.json").read_text(encoding="utf-8"))
    for item in items:
        if item["status"] != "downloaded" or not item.get("entry"):
            continue
        if args.id and item["id"] not in args.id:
            continue
        folder = (root / item["entry"]).parent
        project = folder / "project.json"
        downloader = WebsiteDownloader(item["url"], str(folder), max_workers=4, ai_mode="off")
        try:
            for script in list(folder.rglob("*.js")):
                if "__original_site__" in script.parts or "__source_assets__" in script.parts:
                    continue
                source = script.read_text(encoding="utf-8", errors="replace")
                if "document.write" not in source:
                    continue
                source_url = urljoin(item["url"], script.relative_to(folder).as_posix())
                for url in sorted(_scan_file_for_assets(source, source_url, item["url"])):
                    if urlparse(url).path.lower().endswith((".js", ".mjs")):
                        downloader.download_asset(url, preferred_kind="js")
            if not project.is_file():
                continue
            text = project.read_text(encoding="utf-8")
            localized = downloader.localize_project_custom_css(text)
        finally:
            downloader.close()
            downloader.session.close()
        if localized != text:
            backup = folder / "__original_site__" / "project.before_live_repair.json"
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(str(backup), text)
            atomic_write_text(str(project), localized)
        data = json.loads(localized)
        script = folder / "js/app.js"
        plus2 = str(data.get("version") or "").startswith("2.") and script.is_file()
        if plus2:
            _inject_marked_script(script, localized)
        if localized != text or plus2:
            print(item["id"], "css_changed", localized != text, "plus2", plus2, flush=True)


if __name__ == "__main__":
    main()
