"""Archive validation helpers.

Kept independent from download/package so legacy can import this module without
creating a circular dependency through the download compatibility bridge.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile
from typing import Any, Dict

from .paths import _safe_archive_rel_path


_ZIP_EOCD = b"PK\x05\x06"
_ZIP64_EOCD = b"PK\x06\x06"
_ZIP_EOCD_SEARCH = 22 + 0xFFFF
_DEFAULT_MAX_CENTRAL_DIRECTORY = 32 * 1024 * 1024


def _preflight_zip_container(
    source: Any,
    *,
    max_members: int,
    max_central_directory_size: int,
) -> None:
    """Bound ZIP central-directory metadata before ``ZipFile`` materializes it."""
    if isinstance(source, (str, os.PathLike)):
        try:
            if os.path.getsize(source) > max_central_directory_size + 2 * 1024 * 1024 * 1024:
                raise ValueError("Archive container exceeds configured size limit")
            with open(source, "rb") as fh:
                fh.seek(max(0, os.path.getsize(source) - _ZIP_EOCD_SEARCH))
                tail = fh.read(_ZIP_EOCD_SEARCH)
        except OSError:
            return
    elif isinstance(source, (bytes, bytearray)):
        tail = bytes(source[-_ZIP_EOCD_SEARCH:])
    else:
        return

    eocd = tail.rfind(_ZIP_EOCD)
    if eocd < 0 or len(tail) < eocd + 22:
        return
    _sig, _disk, _disk_start, entries_disk, entries_total, central_size, _central_offset, _comment = struct.unpack_from(
        "<4s4H2LH", tail, eocd
    )
    if entries_total != 0xFFFF and entries_disk != 0xFFFF:
        if entries_total > max_members:
            raise ValueError(f"Archive contains too many members: {entries_total} > {max_members}")
        if central_size > max_central_directory_size:
            raise ValueError("Archive central directory exceeds configured limit")
        return

    # ZIP64 archives carry the authoritative values in the ZIP64 EOCD. If the
    # record is malformed or absent, let ZipFile report it rather than trying
    # to infer a bound from truncated metadata.
    zip64 = tail.rfind(_ZIP64_EOCD, 0, eocd)
    if zip64 < 0 or len(tail) < zip64 + 56:
        return
    values = struct.unpack_from("<4sQ2H2I4Q", tail, zip64)
    entries_total64 = values[7]
    central_size64 = values[8]
    if entries_total64 > max_members:
        raise ValueError(f"Archive contains too many members: {entries_total64} > {max_members}")
    if central_size64 > max_central_directory_size:
        raise ValueError("Archive central directory exceeds configured limit")


def validate_zip_archive(
    source: Any,
    *,
    max_members: int = 10000,
    max_member_size: int = 512 * 1024 * 1024,
    max_total_size: int = 2 * 1024 * 1024 * 1024,
    max_ratio: float = 250.0,
    max_central_directory_size: int = _DEFAULT_MAX_CENTRAL_DIRECTORY,
) -> Dict[str, int]:
    """Validate ZIP traversal, member count, expansion size, and compression ratio."""
    _preflight_zip_container(
        source,
        max_members=max_members,
        max_central_directory_size=max_central_directory_size,
    )
    close_after = False
    if isinstance(source, (str, os.PathLike)):
        zf = zipfile.ZipFile(source)
        close_after = True
    elif isinstance(source, (bytes, bytearray)):
        zf = zipfile.ZipFile(io.BytesIO(bytes(source)))
        close_after = True
    else:
        zf = source
    total = 0
    count = 0
    try:
        infos = zf.infolist()
        if len(infos) > max_members:
            raise ValueError(f"Archive contains too many members: {len(infos)} > {max_members}")
        for info in infos:
            if info.is_dir():
                continue
            _safe_archive_rel_path(info.filename)
            count += 1
            if info.file_size < 0 or info.file_size > max_member_size:
                raise ValueError(f"Archive member too large: {info.filename}")
            total += info.file_size
            if total > max_total_size:
                raise ValueError("Archive expanded size exceeds configured limit")
            compressed = max(1, int(info.compress_size or 0))
            ratio = float(info.file_size) / float(compressed)
            if info.file_size > 1024 * 1024 and ratio > max_ratio:
                raise ValueError(f"Suspicious archive compression ratio: {info.filename}")
        return {"members": count, "total_size": total}
    finally:
        if close_after:
            zf.close()


__all__ = ["validate_zip_archive"]
