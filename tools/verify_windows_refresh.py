"""Verify source code and every file in an EXE-only portable runtime refresh."""

from __future__ import annotations

import argparse
import hashlib
import json
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized(code):
    return code.replace(co_filename="", co_consts=tuple(normalized(value) if isinstance(value, types.CodeType)
                                                      else value for value in code.co_consts))


def embedded(exe):
    archive = CArchiveReader(str(exe))
    return archive.open_embedded_archive(next(name for name in archive.toc if name.startswith("PYZ")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--report-prefix", type=Path, required=True)
    parser.add_argument("--require-module", action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    frozen = embedded(args.exe)
    previous = embedded(args.previous / args.exe.name)
    modules, omitted = [], []
    for source in sorted((root / "cyoa_downloader_app").rglob("*.py")):
        module = ".".join(source.relative_to(root).with_suffix("").parts)
        module = module.removesuffix(".__init__")
        if module not in frozen.toc:
            assert module not in previous.toc, f"Previously packaged module missing: {module}"
            omitted.append(module)
            continue
        expected = compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
        assert normalized(frozen.extract(module)) == normalized(expected), f"Stale frozen module: {module}"
        modules.append({"module": module, "source_sha256": sha(source)})
    for required in ("gui.panel_jobs", "gui.paged_cards", "gui.library_panel", "gui.history_panel"):
        assert "cyoa_downloader_app." + required in frozen.toc
    for required in args.require_module:
        assert required in frozen.toc, f"Required module omitted: {required}"
    exe_hash = sha(args.exe)
    args.report_prefix.with_name(args.report_prefix.name + "_frozen_sources.json").write_text(json.dumps({
        "executable": str(args.exe.resolve()), "sha256": exe_hash,
        "verified_modules": len(modules), "modules": modules, "omitted_previously_too": omitted,
    }, indent=2), encoding="utf-8")

    files = {p.relative_to(args.stage).as_posix(): p for p in args.stage.rglob("*") if p.is_file()}
    old_files = {p.relative_to(args.previous).as_posix(): p for p in args.previous.rglob("*") if p.is_file()}
    assert files.keys() == old_files.keys(), "Portable runtime file set differs"
    manifest = []
    for name, path in sorted(files.items()):
        digest = sha(path)
        if name == args.exe.name:
            assert digest == exe_hash, "Staged EXE differs"
        else:
            assert digest == sha(old_files[name]), f"Changed runtime file: {name}"
        manifest.append({"path": name, "bytes": path.stat().st_size, "sha256": digest})
    for required in ("_internal/_tk_data/tk.tcl", "_internal/_tcl_data/init.tcl",
                     "_internal/tcl86t.dll", "_internal/tk86t.dll"):
        assert required in files and files[required].stat().st_size
    report = {"stage": str(args.stage.resolve()), "files": len(files),
              "bytes": sum(item["bytes"] for item in manifest),
              "runtime_matches_previous_bundle": True, "tcl_tk_verified": True,
              "exe_sha256": exe_hash, "manifest": manifest}
    args.report_prefix.with_name(args.report_prefix.name + "_release_manifest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "manifest"}, indent=2))
    print(f"Verified {len(modules)} source modules; {len(omitted)} omitted in both bundles")


if __name__ == "__main__":
    main()
