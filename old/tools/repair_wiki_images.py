"""Retry remaining MediaWiki image fields through the normal image pipeline."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cyoa_downloader_app.runtime.surface  # noqa: F401
from cyoa_downloader_app.constants.assets import IMAGE_FIELDS
from cyoa_downloader_app.core.atomic_io import atomic_write_text
from cyoa_downloader_app.core.paths import _copytree_merge_safe
from cyoa_downloader_app.download.asset_scan import _extract_image_references
from cyoa_downloader_app.download.image_pipeline import process_images
from cyoa_downloader_app.integrations.offline_viewers.modernizer import _find_marker_script, _inject_marked_script


def wiki_image_fields(project):
    urls = set()
    pending = [project]
    fields = {name.lower() for name in IMAGE_FIELDS}
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in fields and isinstance(child, str):
                    for candidate in _extract_image_references(child, allow_bare_path=True):
                        if re.search(r"(?:^|/)File:", unquote(urlparse(candidate).path), re.IGNORECASE):
                            urls.add(candidate)
                elif isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)
    return sorted(urls)


def replace_fields(value, mapping):
    if isinstance(value, dict):
        result = {}
        fields = {name.lower() for name in IMAGE_FIELDS}
        for key, child in value.items():
            if str(key).lower() in fields and isinstance(child, str) and not any(c in child for c in "<>"):
                refs = _extract_image_references(child, allow_bare_path=True)
                if len(refs) == 1 and next(iter(refs)) in mapping:
                    result[key] = mapping[next(iter(refs))]
                    continue
            result[key] = replace_fields(child, mapping)
        return result
    if isinstance(value, list):
        return [replace_fields(child, mapping) for child in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--id", action="append", default=[])
    args = parser.parse_args()
    root = args.output.resolve()
    items = json.loads((root / "_audit/queue_status.json").read_text(encoding="utf-8"))
    for item in items:
        if item["status"] != "downloaded" or not item.get("entry") or args.id and item["id"] not in args.id:
            continue
        folder = (root / item["entry"]).parent
        path = folder / "project.json"
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        project = json.loads(original)
        urls = wiki_image_fields(project)
        if not urls:
            continue
        work = root / "_audit/wiki_repair" / item["id"]
        work.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"rows": [{"objects": [{"image": url} for url in urls]}]})
        with tempfile.TemporaryDirectory(dir=work) as temporary:
            if not Path(temporary).resolve().is_relative_to(root):
                raise ValueError("Repair temporary folder escaped the output root")
            _embedded, localized, _refs = process_images(
                payload,
                item["url"],
                download=True,
                temp_folder=temporary,
                max_workers=4,
                output_dir=str(work),
                source_url=item["url"],
            )
            values = json.loads(localized)["rows"][0]["objects"]
            mapping = {url: row["image"] for url, row in zip(urls, values, strict=True) if row["image"] != url}
            source = Path(temporary) / "images"
            if source.is_dir():
                _copytree_merge_safe(str(source), str(folder / "images"), label="recovered wiki images")
        if mapping:
            backup = folder / "__original_site__/project.before_wiki_repair.json"
            if not backup.exists():
                atomic_write_text(str(backup), original)
            updated = json.dumps(replace_fields(project, mapping), ensure_ascii=False, separators=(",", ":"))
            atomic_write_text(str(path), updated)
            marker = _find_marker_script(folder)
            if marker is not None:
                _inject_marked_script(marker, updated)
        report = {
            "requested": len(urls),
            "recovered": len(mapping),
            "remaining": [url for url in urls if url not in mapping],
        }
        atomic_write_text(str(folder / "wiki_repair_report.json"), json.dumps(report, indent=2))
        print(item["id"], "recovered", len(mapping), "of", len(urls), flush=True)


if __name__ == "__main__":
    main()
