"""Check release versions, local Markdown links, and source-tree hygiene."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check(root):
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    paths = sorted({Path(value.decode("utf-8")) for value in listing.stdout.split(b"\0") if value})
    errors = []
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append("VERSION must contain a three-part release version")
    metadata = ast.parse((root / "old/cyoa_downloader_app/app_info.py").read_text(encoding="utf-8"))
    values = {node.targets[0].id: ast.literal_eval(node.value)
              for node in metadata.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    # The Python reference under old/ carries its own VERSION; the root VERSION
    # tracks the Rust release.
    py_version_file = root / "old/VERSION"
    py_version = (py_version_file.read_text(encoding="utf-8").strip()
                  if py_version_file.is_file() else version)
    if values.get("_APP_VERSION") != py_version or values.get("_STABILIZATION_PATCH_ID") != "CYOA-v" + py_version:
        errors.append("Application metadata does not match VERSION")
    readme = (root / "README.md").read_text(encoding="utf-8")
    cargo = (root / "Cargo.toml").read_text(encoding="utf-8")
    rust_version = re.search(r"(?ms)^\[workspace\.package\].*?^version\s*=\s*\"([^\"]+)\"", cargo)
    if not rust_version or "version-v" + rust_version.group(1) + "-" not in readme:
        errors.append("README version badge does not match the Rust workspace version")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(r"^## v" + re.escape(version) + r"\s", changelog, re.MULTILINE):
        errors.append("CHANGELOG has no heading for VERSION")
    forbidden_roots = {"build", "dist", "downloads", "release", "release-artifacts", "live_test_audit"}
    forbidden_suffixes = {".exe", ".dll", ".log", ".pyc", ".part", ".tmp"}
    markdown = 0
    for relative in paths:
        path = root / relative
        if not path.is_file():
            continue
        if relative.parts[0] in forbidden_roots or path.suffix.lower() in forbidden_suffixes:
            errors.append(f"Generated artifact is not excluded: {relative.as_posix()}")
        if path.suffix.lower() != ".md":
            continue
        markdown += 1
        text = path.read_text(encoding="utf-8")
        if re.search(r"C:[/\\]Users[/\\]|E:[/\\]Download[/\\]_", text, re.IGNORECASE):
            errors.append(f"Machine-specific path in Markdown: {relative.as_posix()}")
        # Detect untranslated terms from the historical reports. This is a
        # focused regression check, not a general natural-language classifier.
        if re.search(r"\b(?:pengukuran|sebelum|sesudah|dilewati|pengguna|berkas|optimisasi|lulus)\b", text, re.IGNORECASE):
            errors.append(f"Untranslated report text in Markdown: {relative.as_posix()}")
        for link in re.finditer(r"\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", text):
            target = link[1].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            destination = path.parent / unquote(parsed.path)
            if not destination.exists():
                errors.append(f"Broken local Markdown link in {relative.as_posix()}: {target}")
    return version, markdown, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    version, count, errors = check(args.root.resolve())
    if errors:
        for error in errors:
            print("FAIL " + error)
        raise SystemExit(1)
    print(f"PASS v{version}: {count} English Markdown files, local links, and source-tree hygiene")


if __name__ == "__main__":
    main()
