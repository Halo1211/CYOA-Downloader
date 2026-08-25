"""Output-folder safety and cleanup helpers."""

from __future__ import annotations

import os
import re
import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from .atomic_io import interprocess_file_lock
from ..logging_setup import logger


@contextmanager
def output_directory_lease(
    output_dir: str,
    *,
    timeout: float = 0.25,
    lock_root: Optional[str] = None,
) -> Iterator[str]:
    """Exclusively lease one canonical output root across app processes."""
    canonical = os.path.realpath(os.path.abspath(output_dir or os.getcwd()))
    lock_identity = os.path.normcase(canonical)
    digest = hashlib.sha256(os.fsencode(lock_identity)).hexdigest()
    lease_root = lock_root or os.path.join(
        os.path.expanduser("~"), ".cyoa_downloader", "run_locks"
    )
    lease_path = os.path.join(os.path.abspath(lease_root), digest + ".lease")
    guard = interprocess_file_lock(lease_path, timeout=timeout)
    try:
        guard.__enter__()
    except TimeoutError as exc:
        raise RuntimeError(
            "Output directory is already in use by another CYOA Downloader "
            f"process: {canonical}"
        ) from exc
    try:
        yield canonical
    finally:
        guard.__exit__(None, None, None)


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
    """Remove only downloader temporary files created/modified by this run.

    A user may legitimately download or create an asset whose name ends in
    ``.part``.  Atomic writers in this project use the more specific
    ``<target>.<pid>.<thread>.part`` suffix, so cleanup must match that shape
    instead of treating every ``*.part`` file as disposable.
    """
    if not root or not os.path.isdir(root):
        return 0
    removed = 0
    for current_root, _dirs, files in os.walk(root):
        for name in files:
            # Atomic downloads use ``<target>.<pid>.<thread>.part``. Do not
            # delete user content such as ``asset.part``.
            if not re.fullmatch(r".+\.\d+\.\d+\.part", name):
                continue
            path = os.path.join(current_root, name)
            try:
                if os.path.getmtime(path) >= since - 5.0:
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                logger.debug(f"Could not clean partial file {path}: {exc}")
    return removed


__all__ = [
    "output_directory_lease",
    "prepare_clean_output_folder",
    "_cleanup_recent_part_files",
]
