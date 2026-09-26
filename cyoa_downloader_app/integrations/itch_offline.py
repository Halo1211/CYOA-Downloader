"""Build reusable offline folders from HTML5 archives returned by itch-dl."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from ..core.archive import validate_zip_archive
from ..core.atomic_io import atomic_write_text
from ..core.paths import _safe_archive_join, _safe_archive_rel_path
from .offline_viewers.injector import _localize_preserved_index_assets

HTML5_MARKER_FILENAME = ".cyoa_itch_archive.json"


class EncryptedHtml5ArchiveError(ValueError):
    """The original ZIP is available, but extraction needs a password."""


def _archive_fingerprint(archive: Path) -> str:
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _html_entry(archive: zipfile.ZipFile) -> str | None:
    entries = []
    for member in archive.infolist():
        if member.is_dir():
            continue
        relative = _safe_archive_rel_path(member.filename)
        if relative.lower().endswith("/index.html") or relative.lower() == "index.html":
            entries.append(relative)
    return min(entries, key=lambda entry: (entry.count("/"), len(entry))) if entries else None


def _extract_validated(archive: Path, destination: Path) -> str | None:
    validate_zip_archive(archive, max_members=20000,
                         max_member_size=1024**3, max_total_size=2 * 1024**3)
    with zipfile.ZipFile(archive) as source:
        if any(member.flag_bits & 0x1 for member in source.infolist()):
            raise EncryptedHtml5ArchiveError("ZIP is password-protected")
        entry = _html_entry(source)
        if entry is None:
            return None
        seen: set[str] = set()
        for member in source.infolist():
            relative = _safe_archive_rel_path(member.filename.rstrip("/\\") if member.is_dir() else member.filename)
            identity = relative.casefold()
            if identity in seen:
                raise ValueError("HTML5 archive contains duplicate paths")
            seen.add(identity)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("HTML5 archive contains a symbolic link")
            target = Path(_safe_archive_join(str(destination), relative))
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
    return entry


def materialize_itch_html5_archive(
    archive_path: str | Path,
    source_url: str,
    *,
    fetcher=None,
) -> tuple[str | None, bool, bool]:
    """Return (offline entry, reused, fully localized) for an HTML5 ZIP.

    A non-HTML5 ZIP returns ``(None, False, True)``. Existing output is reused
    only when its archive digest matches; partial outputs retry missing assets.
    """
    supplied = Path(archive_path)
    if supplied.is_symlink():
        raise ValueError("HTML5 archive path must not be a symbolic link")
    archive = supplied.resolve(strict=True)
    if archive.suffix.lower() != ".zip":
        return None, False, True
    digest = _archive_fingerprint(archive)
    base = archive.with_name(archive.stem + "_offline")
    destination = base
    marker_path = destination / HTML5_MARKER_FILENAME
    if destination.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if not isinstance(marker, dict):
                marker = {}
        except (OSError, ValueError):
            marker = {}
        if marker.get("sha256") != digest:
            destination = archive.with_name(archive.stem + "_offline_" + digest[:12])
            marker_path = destination / HTML5_MARKER_FILENAME
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError("HTML5 offline destination is not a regular directory")
    if destination.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if not isinstance(marker, dict):
                raise TypeError("HTML5 offline manifest must be a JSON object")
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("HTML5 offline manifest is unreadable") from exc
        if marker.get("sha256") != digest:
            raise ValueError("HTML5 offline destination belongs to another archive")
        entry = marker.get("entry")
        if not isinstance(entry, str):
            raise ValueError("HTML5 offline manifest has no entry point")
        index = Path(_safe_archive_join(str(destination), entry))
        if not index.is_file():
            raise ValueError("HTML5 offline entry point is missing")
        if marker.get("complete"):
            return str(index), True, True
        (index.parent / "failed_assets.txt").unlink(missing_ok=True)
    else:
        with tempfile.TemporaryDirectory(prefix="itch-html5-", dir=archive.parent) as staging:
            staging_path = Path(staging)
            entry = _extract_validated(archive, staging_path)
            if entry is None:
                return None, False, True
            atomic_write_text(
                str(staging_path / HTML5_MARKER_FILENAME),
                json.dumps({"sha256": digest, "entry": entry, "complete": False}),
            )
            os.rename(staging_path, destination)
        index = Path(_safe_archive_join(str(destination), entry))
    original_html = index.read_text(encoding="utf-8-sig")
    localized_html = _localize_preserved_index_assets(
        original_html, source_url, str(index.parent), fetcher=fetcher,
    )
    atomic_write_text(str(index), localized_html)
    complete = not (index.parent / "failed_assets.txt").exists()
    atomic_write_text(
        str(marker_path),
        json.dumps({"sha256": digest, "entry": entry, "complete": complete}),
    )
    return str(index), False, complete


__all__ = [
    "HTML5_MARKER_FILENAME",
    "EncryptedHtml5ArchiveError",
    "materialize_itch_html5_archive",
]
