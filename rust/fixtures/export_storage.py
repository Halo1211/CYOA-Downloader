#!/usr/bin/env python3
"""Export ZIP validation and atomic JSON parity fixtures from the Python reference."""
from __future__ import annotations
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cyoa_downloader_app.core.archive import validate_zip_archive
from cyoa_downloader_app.core.atomic_io import atomic_write_bytes

OUT = Path(__file__).resolve().parent / "storage"
ZIPS = OUT / "zips"
ZIPS.mkdir(parents=True, exist_ok=True)


def make(name: str, entries: list[tuple[str, bytes, int | None]]) -> None:
    with zipfile.ZipFile(ZIPS / name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, payload, mode in entries:
            info = zipfile.ZipInfo(filename)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, payload)


cases = {
    "normal.zip": [("folder/chapter.txt", b"hello", None)],
    "traversal.zip": [("../escape.txt", b"x", None)],
    "absolute.zip": [("/x", b"x", None)],
    "drive.zip": [("C:x", b"x", None)],
    "device.zip": [("CON.txt", b"x", None)],
    "case_duplicate.zip": [("A.txt", b"a", None), ("a.txt", b"b", None)],
    "symlink.zip": [("link", b"target", 0o120777)],
    "ratio_bomb.zip": [("zeros.bin", b"\0" * (20 * 1024 * 1024), None)],
    "too_many.zip": [(f"f{i:05}.txt", b"", None) for i in range(10001)],
}
for filename, entries in cases.items():
    make(filename, entries)

verdicts = {}
for filename in cases:
    try:
        verdicts[filename] = {"result": "accept", "members": validate_zip_archive(ZIPS / filename)["members"]}
    except Exception as exc:
        verdicts[filename] = {"result": "error", "class": type(exc).__name__, "message_prefix": str(exc)[:100]}
(OUT / "verdicts.json").write_text(json.dumps(verdicts, indent=2, ensure_ascii=False), encoding="utf-8")

samples = {
    "unicode": {"greeting": "????? ??", "quote": 'a"b', "slash": "a/b"},
    "nested": {"items": [1, {"enabled": True, "nothing": None}], "empty": {}},
    "ordering": {"z": 1, "a": 2, "middle": {"second": 2, "first": 1}},
}
for name, value in samples.items():
    expected = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    atomic_write_bytes(str(OUT / f"json_{name}.expected"), expected)
(OUT / "json_samples.json").write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
