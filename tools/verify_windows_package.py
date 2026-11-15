"""Verify an extracted distribution against its ZIP and current Python source."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import marshal
import types
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]


def stream_hash(stream):
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return stream_hash(stream)


def equivalent(left, right):
    """Compare bytecode including line/exception tables, ignoring build paths."""
    if isinstance(left, types.CodeType) and isinstance(right, types.CodeType):
        attributes = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals", "co_stacksize",
                      "co_flags", "co_code", "co_names", "co_varnames", "co_freevars", "co_cellvars",
                      "co_firstlineno", "co_linetable", "co_exceptiontable", "co_name", "co_qualname")
        return (all(getattr(left, name, None) == getattr(right, name, None) for name in attributes)
                and equivalent(left.co_consts, right.co_consts))
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def zip_members_equal(body, target):
    with zipfile.ZipFile(io.BytesIO(body)) as left, zipfile.ZipFile(target) as right:
        names = left.namelist()
        if len(names) != len(set(names)) or set(names) != set(right.namelist()) or len(names) != len(right.namelist()):
            return False
        return (left.testzip() is None and right.testzip() is None
                and all(left.getinfo(name).CRC == right.getinfo(name).CRC and left.read(name) == right.read(name) for name in names))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("extracted", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--compare-runtime", type=Path, help="Compare standalone embedded data/binaries with a portable _internal folder")
    args = parser.parse_args()
    extracted = args.extracted.resolve(strict=True)
    runtime = []
    with zipfile.ZipFile(args.archive) as zipped:
        assert zipped.testzip() is None, "Distribution ZIP CRC failed"
        for member in zipped.infolist():
            if member.is_dir():
                continue
            path = (extracted / member.filename).resolve(strict=True)
            assert path.is_relative_to(extracted), member.filename
            with zipped.open(member) as stream:
                digest = stream_hash(stream)
            assert path.stat().st_size == member.file_size and file_hash(path) == digest, member.filename
            runtime.append({"path": member.filename, "size": member.file_size, "sha256": digest})
    candidates = list(extracted.rglob("CYOA Downloader.exe"))
    assert len(candidates) == 1, "Expected exactly one distribution executable"
    executable = candidates[0]
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    modules = []
    # These historical compatibility shims are not imported by the application.
    # Preserve the established package boundary; do not silently exempt new code.
    unused_compatibility = {"cyoa_downloader_app.gui.patches", "cyoa_downloader_app.integrations._bridge"}
    for source in sorted((ROOT / "cyoa_downloader_app").rglob("*.py")):
        parts = source.relative_to(ROOT).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        if module in unused_compatibility and module not in pyz.toc:
            continue
        assert module in pyz.toc, f"Missing frozen module: {module}"
        frozen = pyz.extract(module)
        current = compile(source.read_bytes(), str(source), "exec", optimize=0, dont_inherit=True)
        assert equivalent(frozen, current), f"Frozen source mismatch: {module}"
        modules.append({"module": module, "source_sha256": file_hash(source)})
    source = ROOT / "cyoa_downloader.py"
    assert equivalent(marshal.loads(archive.extract("cyoa_downloader")), compile(source.read_bytes(), str(source), "exec", dont_inherit=True))
    required = ("_tcl_data/init.tcl", "_tk_data/tk.tcl", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "_sqlite3.pyd", "sqlite3.dll")
    portable = executable.parent / "_internal"
    embedded_names = {name.replace("\\", "/") for name in archive.toc}
    for member in required:
        assert (portable / member).is_file() if portable.is_dir() else member in embedded_names, member
    embedded_runtime = []
    compare = args.compare_runtime.resolve(strict=True) if args.compare_runtime else None
    for name, entry in archive.toc.items():
        if entry[-1] not in {"b", "x"}:
            continue
        body = archive.extract(name)
        digest = hashlib.sha256(body).hexdigest()
        relative = name.replace("\\", "/")
        match = "sha256"
        if compare is not None:
            target = (compare / relative).resolve(strict=True)
            assert target.is_relative_to(compare), relative
            if file_hash(target) != digest:
                assert relative.endswith(".zip") and zip_members_equal(body, target), f"Embedded runtime mismatch: {relative}"
                match = "zip_member_bytes_and_crc"
        embedded_runtime.append({"path": relative, "size": len(body), "sha256": digest, "equivalence": match})
    result = {"archive": str(args.archive.resolve()), "archive_sha256": file_hash(args.archive),
              "executable": str(executable), "executable_sha256": file_hash(executable),
              "zip_crc": "pass", "frozen_application_modules": len(modules), "modules": modules,
              "required_runtime": list(required), "runtime_files": runtime,
              "embedded_runtime": embedded_runtime, "compared_runtime": str(compare) if compare else None}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), "utf-8")
    print(f"PASS: {len(modules)} frozen modules, main script, Tcl/Tk/SQLite, {len(runtime)} extracted ZIP members")


if __name__ == "__main__":
    main()
