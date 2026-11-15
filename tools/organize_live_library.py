"""Flatten completed queue outputs to readable title/index.html folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyoa_downloader_app.core.atomic_io import atomic_write_bytes
from cyoa_downloader_app.core.output import output_directory_lease
from cyoa_downloader_app.core.paths import _is_link_or_junction
from tools.run_live_queue import catalogue, human_folder_name, unique_folder_name, write_json


def inventory(folder):
    """Verify file identities across same-volume directory renames."""
    rows = []
    for current, directories, files in os.walk(folder):
        for name in directories + files:
            path = Path(current) / name
            if _is_link_or_junction(str(path)):
                raise ValueError(f"Linked content cannot be reorganized: {path}")
        for name in files:
            path = Path(current) / name
            stat = path.stat()
            rows.append((path.relative_to(folder).as_posix(), stat.st_ino, stat.st_size, stat.st_mtime_ns))
    return sorted(rows)


def plan_layout(root, records):
    if _is_link_or_junction(str(root)) or _is_link_or_junction(str(root / "_audit")):
        raise ValueError("The library and audit roots must not be redirected")
    if any(row.get("status") == "running" for row in records):
        raise ValueError("Finish active queue jobs before organizing the library")
    used = {path.name.casefold() for path in root.iterdir()}
    claimed = set()
    plan = []
    for row in records:
        if row.get("status") != "downloaded":
            continue
        identity, folder = str(row.get("id", "")), str(row.get("folder", ""))
        if (not re.fullmatch(r"[a-f0-9]{10}", identity) or identity in claimed or not folder
                or folder in {".", ".."} or "/" in folder or "\\" in folder
                or folder.casefold() in {"_audit", "_program", "_releases"}):
            raise ValueError("Invalid checkpoint identity or folder")
        claimed.add(identity)
        wrapper = root / folder
        relative_entry = Path(str(row.get("entry", "")))
        if relative_entry.is_absolute() or ".." in relative_entry.parts:
            raise ValueError("Entry must be a contained relative path")
        entry = root / relative_entry
        if (not entry.resolve().is_relative_to(wrapper) or not entry.is_file()
                or entry.name != "index.html" or _is_link_or_junction(str(wrapper))):
            raise ValueError(f"Invalid or missing entry for {identity}")
        source = entry.parent
        current = source
        while current != root:
            if _is_link_or_junction(str(current)):
                raise ValueError("Entry path contains a redirected directory")
            current = current.parent
        name = human_folder_name(str(row.get("title") or folder))
        if source == wrapper and (source.name == name or row.get("layout_version") == 2):
            destination = source
        else:
            destination = root / unique_folder_name(name, used)
        if destination.parent != root or source.stat().st_dev != root.stat().st_dev:
            raise ValueError("Only same-volume moves inside the library are supported")
        if destination != source and destination.exists():
            raise FileExistsError(destination)
        plan.append({"id": identity, "old_folder": folder, "source": str(source),
                     "destination": str(destination), "folder": destination.name,
                     "entry": (destination / "index.html").relative_to(root).as_posix(),
                     "changed": source != destination})
    return plan


def _remap(value, mappings):
    if isinstance(value, dict):
        return {key: _remap(item, mappings) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, mappings) for item in value]
    if isinstance(value, str):
        for old, new in mappings:
            if value == old or value.startswith((old + "/", old + "\\")):
                return new + value[len(old):]
    return value


def apply_layout(root, records, plan):
    audit = root / "_audit"
    changed = [item for item in plan if item["changed"]]
    if not changed:
        return {"changed": 0, "files_verified": 0}
    backup = audit / "layout-backups" / uuid.uuid4().hex
    if backup.resolve().parent.parent != audit:
        raise ValueError("Invalid backup destination")
    backup.mkdir(parents=True)
    metadata = {root / "index.html", audit / "queue_status.json", audit / "retry_queue.csv"}
    for item in changed:
        for suffix in (".json", ".job.json", ".viewer.json"):
            metadata.add(audit / (item["id"] + suffix))
    originals = {}
    absent = {path for path in metadata if not path.exists()}
    for path in metadata:
        if path.exists():
            if _is_link_or_junction(str(path)):
                raise ValueError("Linked metadata cannot be reorganized")
            originals[path] = path.read_bytes()
            saved = backup / "metadata" / path.relative_to(root)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(originals[path])
    write_json(backup / "plan.json", plan)
    moves, mappings, verified = [], [], []
    try:
        for item in changed:
            source, destination = Path(item["source"]), Path(item["destination"])
            wrapper = root / item["old_folder"]
            before = inventory(source)
            if destination.exists():
                raise FileExistsError(destination)
            source.rename(destination)
            moves.append((source, destination))
            if inventory(destination) != before:
                raise RuntimeError("Viewer file identities changed during reorganization")
            verified.append({"id": item["id"], "files": len(before), "bytes": sum(row[2] for row in before),
                "identity_manifest_sha256": hashlib.sha256(json.dumps(before).encode()).hexdigest()})
            for old in (source, wrapper):
                mappings.extend([(str(old), str(destination)),
                    (old.relative_to(root).as_posix(), destination.name),
                    (old.relative_to(root).as_posix().replace("/", "\\"), destination.name)])
            if wrapper != source:
                saved_wrapper = backup / "wrappers" / item["id"]
                saved_wrapper.parent.mkdir(exist_ok=True)
                wrapper.rename(saved_wrapper)
                moves.append((wrapper, saved_wrapper))
        mappings.sort(key=lambda pair: len(pair[0]), reverse=True)
        for path, content in originals.items():
            if path.suffix == ".json" and path.name != "queue_status.json":
                write_json(path, _remap(json.loads(content), mappings))
        updated = _remap(records, mappings)
        for row in updated:
            item = next((item for item in plan if item["id"] == row["id"]), None)
            if item is not None:
                row.update(folder=item["folder"], entry=item["entry"], output=str(root / item["folder"]), layout_version=2)
                write_json(audit / (row["id"] + ".json"), row)
        catalogue(root, updated, audit)
        result = {"changed": len(changed), "files_verified": sum(row["files"] for row in verified),
                  "bytes_verified": sum(row["bytes"] for row in verified), "backup": str(backup),
                  "verification": verified, "state": "completed"}
        write_json(backup / "result.json", result)
        return result
    except BaseException:
        for source, destination in reversed(moves):
            destination.rename(source)
        for path, content in originals.items():
            atomic_write_bytes(str(path), content)
        for path in absent:
            path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply after validating every planned move")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.output.absolute()
    if _is_link_or_junction(str(root)):
        parser.error("The library root must not be redirected")
    root = root.resolve(strict=True)
    audit = root / "_audit"
    with output_directory_lease(str(root)), output_directory_lease(str(audit)):
        records = json.loads((audit / "queue_status.json").read_text(encoding="utf-8"))
        plan = plan_layout(root, records)
        result = apply_layout(root, records, plan) if args.apply else {"planned": sum(item["changed"] for item in plan)}
        if args.report:
            args.report.write_text(json.dumps({"plan": plan, "result": result}, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "verification"}, indent=2))


if __name__ == "__main__":
    main()
