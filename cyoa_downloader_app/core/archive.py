"""Archive validation helpers.

Kept independent from download/package so legacy can import this module without
creating a circular dependency through the download compatibility bridge.
"""

from __future__ import annotations

import io
import os
import zipfile
from typing import Any, Dict

from .paths import _safe_archive_rel_path


def validate_zip_archive(
    source: Any,
    *,
    max_members: int = 10000,
    max_member_size: int = 512 * 1024 * 1024,
    max_total_size: int = 2 * 1024 * 1024 * 1024,
    max_ratio: float = 250.0,
) -> Dict[str, int]:
    """Validate ZIP traversal, member count, expansion size, and compression ratio."""
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
