"""Output-folder safety and cleanup helpers."""

from __future__ import annotations

import os
from datetime import datetime

from ..logging_setup import logger


def prepare_clean_output_folder(folder: str) -> None:
    """Create a clean output folder without silently deleting pre-existing data."""
    target = os.path.abspath(folder)
    if os.path.isdir(target) and os.listdir(target):
        backup = target + ".pre_v46_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = 1
        while os.path.exists(backup):
            backup = target + f".pre_v46_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suffix}"
            suffix += 1
        os.replace(target, backup)
        logger.warning(f"Existing output folder preserved as: {backup}")
    elif os.path.isfile(target):
        raise ValueError(f"Output path is a file: {target}")
    os.makedirs(target, exist_ok=True)


def _cleanup_recent_part_files(root: str, since: float) -> int:
    """Remove only .part files created/modified by the current run."""
    if not root or not os.path.isdir(root):
        return 0
    removed = 0
    for current_root, _dirs, files in os.walk(root):
        for name in files:
            # Atomic downloads use ``<target>.<pid>.<thread>.part``. A real
            # asset may legitimately contain ".part" in its basename.
            if not name.endswith(".part"):
                continue
            path = os.path.join(current_root, name)
            try:
                if os.path.getmtime(path) >= since - 5.0:
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                logger.debug(f"Could not clean partial file {path}: {exc}")
    return removed


__all__ = ["prepare_clean_output_folder", "_cleanup_recent_part_files"]
