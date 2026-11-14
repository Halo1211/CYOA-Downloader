"""Bounded file-backed payloads for project media downloads."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass

from ..logging_setup import logger
from .atomic_io import decoded_response_content_length, validate_response_content_length
from .cancellation import _raise_if_cancelled

MEDIA_CHUNK_BYTES = 64 * 1024


class ResponseBodySizeError(OSError):
    """A decoded response does not match its declared/configured length."""


@dataclass(frozen=True)
class FilePayload:
    path: str
    size: int
    sha256: str
    prefix: bytes

    def __len__(self):
        return self.size


def spool_chunks(chunks, directory, *, response=None, on_chunk=None, max_bytes=None) -> FilePayload:
    """Hash and spool decoded chunks, removing incomplete files on any failure."""
    _raise_if_cancelled()
    fd, part = tempfile.mkstemp(prefix="media-", suffix=".part", dir=directory)
    complete = part[:-5] + ".asset"
    total = 0
    digest = hashlib.sha256()
    prefix = b""
    expected = decoded_response_content_length(response) if response is not None else None
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in chunks:
                _raise_if_cancelled()
                if not chunk:
                    continue
                total += len(chunk)
                if (max_bytes is not None and total > max_bytes) or (expected is not None and total > expected):
                    raise ResponseBodySizeError("Response body exceeds declared or configured size")
                handle.write(chunk)
                digest.update(chunk)
                if len(prefix) < 512:
                    prefix += chunk[:512 - len(prefix)]
                if on_chunk is not None:
                    on_chunk(len(chunk))
            if response is not None:
                try:
                    validate_response_content_length(response, total)
                except OSError as exc:
                    raise ResponseBodySizeError(str(exc)) from exc
            _raise_if_cancelled()
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, complete)
        return FilePayload(complete, total, digest.hexdigest(), prefix)
    except BaseException:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Could not remove partial media %s: %s", part, exc)
        raise


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(MEDIA_CHUNK_BYTES), b""):
            _raise_if_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy_file(source, target):
    """Copy without materializing the body; retain the old target on failure."""
    target = os.path.abspath(target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, part = tempfile.mkstemp(prefix=".media-", suffix=".part", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "wb") as output, open(source, "rb") as input_file:
            for chunk in iter(lambda: input_file.read(MEDIA_CHUNK_BYTES), b""):
                _raise_if_cancelled()
                output.write(chunk)
            _raise_if_cancelled()
            output.flush()
            os.fsync(output.fileno())
        os.replace(part, target)
    except BaseException:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Could not remove partial copy %s: %s", part, exc)
        raise


def atomic_write_text_chunks(target, chunks):
    """Write UTF-8 chunks atomically, preserving an old file on cancel/failure."""
    target = os.path.abspath(target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, part = tempfile.mkstemp(prefix=".embed-", suffix=".part", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "wb") as output:
            for chunk in chunks:
                _raise_if_cancelled()
                output.write(chunk.encode("utf-8"))
            _raise_if_cancelled()
            output.flush()
            os.fsync(output.fileno())
        os.replace(part, target)
        return target
    except BaseException:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Could not remove partial embedded JSON %s: %s", part, exc)
        raise
