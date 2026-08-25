"""Atomic file write helpers."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from ..logging_setup import logger


@contextmanager
def interprocess_file_lock(path: str, timeout: float = 10.0) -> Iterator[None]:
    """Serialize a short file transaction across processes.

    The sibling ``.lock`` file is intentionally persistent: deleting a lock
    file after release creates an inode race where a second process can lock a
    newly-created file while a third still holds the old one.
    """
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or os.getcwd(), exist_ok=True)
    lock_path = target + ".lock"
    deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
    handle = open(lock_path, "a+b", buffering=0)
    acquired = False
    backend = ""
    try:
        if os.path.getsize(lock_path) == 0:
            handle.write(b"\0")
        while not acquired:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    backend = "msvcrt"
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    backend = "fcntl"
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for file lock: {lock_path}")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if backend == "msvcrt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif backend == "fcntl":
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                logger.debug(f"Could not release file lock {lock_path}: {exc}")
        handle.close()

def atomic_write_bytes(path: str, data: bytes) -> str:
    """Write bytes to a sibling .part file, fsync, then atomically replace."""
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or os.getcwd(), exist_ok=True)
    part = target + f".{os.getpid()}.{threading.get_ident()}.part"
    try:
        with open(part, "wb") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError as exc:
                logger.debug(f"fsync unavailable for {part}: {exc}")
        os.replace(part, target)
        return target
    except Exception:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError as cleanup_exc:
            logger.debug(f"Could not remove partial file {part}: {cleanup_exc}")
        raise

def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> str:
    """Atomic text-file counterpart to atomic_write_bytes()."""
    return atomic_write_bytes(path, str(text).encode(encoding))



def validate_response_content_length(response: Any, actual_length: int) -> Optional[int]:
    """Validate an uncompressed response body against Content-Length.

    Requests transparently decompresses gzip/br content, so wire Content-Length
    cannot safely be compared when Content-Encoding is non-identity.
    """
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Content-Length") or headers.get("content-length")
    encoding = str(headers.get("Content-Encoding") or headers.get("content-encoding") or "").strip().lower()
    if raw in (None, "") or encoding not in {"", "identity"}:
        return None
    try:
        expected = int(raw)
        actual = int(actual_length)
    except (TypeError, ValueError):
        return None
    if expected < 0:
        return None
    if actual != expected:
        raise IOError(f"Incomplete response body: expected {expected} bytes, received {actual}")
    return expected


__all__ = [
    'atomic_write_bytes', 'atomic_write_text', 'interprocess_file_lock',
    'validate_response_content_length',
]
