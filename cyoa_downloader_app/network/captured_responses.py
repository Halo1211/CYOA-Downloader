"""Run-scoped, file-backed reuse of completed browser asset responses."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from ..core.cancellation import _raise_if_cancelled
from ..core.media_io import MEDIA_CHUNK_BYTES, FilePayload, spool_chunks
from ..logging_setup import logger


class RuntimeCaptureStorageError(Exception):
    """A job-owned capture file could not be retained."""


@dataclass(frozen=True)
class _CapturedResponse:
    payload: FilePayload
    headers: dict[str, str]


class CapturedResponseStore:
    """Bound retained files, not Playwright's single buffered body RPC.

    Only completed, uncompressed GET 200 responses with a bounded declared
    length are eligible. Dishonest headers can still overshoot during body().
    Unknown/large/compressed/partial responses use the ordinary downloader.
    """

    def __init__(self, directory=None, *, max_body_bytes=2 * 1024 * 1024,
                 max_total_bytes=32 * 1024 * 1024, max_entries=128):
        self._spool = tempfile.TemporaryDirectory(prefix=".cyoa-runtime-", dir=directory)
        self._entries = {}
        self._handles = {}
        self._capturing = False
        self._closed = False
        self._failure = None
        self.max_body_bytes = max(0, int(max_body_bytes))
        self.max_total_bytes = max(0, int(max_total_bytes))
        self.max_entries = max(0, int(max_entries))
        self.captured_count = self.captured_bytes = 0
        self.reused_count = self.reused_bytes = 0
        self.reused_urls = set()

    @property
    def urls(self):
        return tuple(self._entries)

    def capture(self, response):
        _raise_if_cancelled()
        # Nested Playwright event dispatch must not stack buffered body RPCs.
        if self._closed or self._failure or self._capturing:
            return False
        headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        url = str(response.url)
        parsed = urlparse(url)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or
                str(response.request.method).upper() != "GET" or response.status != 200 or
                headers.get("content-encoding", "identity").lower().strip() not in {"", "identity"} or
                "content-range" in headers or url in self._entries):
            return False
        try:
            size = int(headers.get("content-length", ""))
        except (TypeError, ValueError, OverflowError):
            return False
        if (not 0 < size <= self.max_body_bytes or len(self._entries) >= self.max_entries or
                self.captured_bytes + size > self.max_total_bytes):
            return False
        self._capturing = True
        try:
            body = response.body()
            _raise_if_cancelled()
            if len(body) != size:
                return False
            try:
                payload = spool_chunks((body[i:i + MEDIA_CHUNK_BYTES] for i in range(0, size, MEDIA_CHUNK_BYTES)),
                                       self._spool.name, max_bytes=self.max_body_bytes)
            except OSError as exc:
                # Event handlers record the failure; the owner raises it after
                # returning from browser dispatch, outside recoverable PW errors.
                self._failure = exc
                return False
            self._entries[url] = _CapturedResponse(payload, headers)
            self.captured_count += 1
            self.captured_bytes += size
            return True
        finally:
            self._capturing = False

    def raise_if_failed(self):
        if self._failure is not None:
            raise RuntimeCaptureStorageError(f"Could not retain runtime response: {self._failure}") from self._failure

    def take(self, url):
        _raise_if_cancelled()
        entry = self._entries.pop(url, None) if not self._closed else None
        if entry is None:
            return None
        payload = entry.payload
        handle = open(payload.path, "rb")  # noqa: SIM115 -- owned by response.close and store.close.
        self._handles[handle] = payload.path
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers.update(entry.headers)
        response.headers.pop("Content-Encoding", None)
        response.headers.pop("Transfer-Encoding", None)
        response.headers["Content-Length"] = str(payload.size)
        response.raw = handle
        encoding = requests.utils.get_encoding_from_headers(response.headers)
        response.encoding = "utf-8" if str(encoding or "").lower() in {"", "iso-8859-1", "latin-1"} else encoding
        original_close = response.close
        def close():
            try:
                original_close()
            finally:
                handle.close()
                self._handles.pop(handle, None)
                self._remove(payload.path)
        response.close = close
        self.reused_count += 1
        self.reused_bytes += payload.size
        self.reused_urls.add(url)
        return response

    @staticmethod
    def _remove(path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Runtime response file cleanup failed: %s", exc)

    def close(self):
        if self._closed:
            return
        self._closed = True
        for handle in tuple(self._handles):
            handle.close()
        self._handles.clear()
        self._entries.clear()
        self._spool.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
