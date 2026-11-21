"""Job-local, header-only handoff from completed native GETs to history."""

from __future__ import annotations

from contextvars import ContextVar

from ..core.atomic_io import decoded_response_content_length
from ..logging_setup import logger

_CAPTURE = ContextVar("cyoa_response_metadata", default=None)
_OPTIONAL_METADATA_ERRORS = (Exception,)


class ResponseMetadataCapture:
    """Retain at most one small header snapshot, never a response or body."""

    def __init__(self, url, context):
        self.url, self.context = url, context
        self._candidate = None

    def __enter__(self):
        self._candidate = None
        self._initial = None
        try:
            self._initial = self.context(self.url)
        except _OPTIONAL_METADATA_ERRORS as exc:
            logger.debug("Response metadata context unavailable: %s", exc)
        self._token = _CAPTURE.set(self)
        return self

    def __exit__(self, kind, value, traceback):
        _CAPTURE.reset(self._token)
        if kind is not None:
            self._candidate = None

    def take(self, url):
        candidate, self._candidate = self._candidate, None
        if url != self.url or candidate is None:
            return None
        try:
            values, expected = candidate
            current = self.context(url)
            if current[0] is expected[0] and current[1] == expected[1]:
                return dict(values)
        except _OPTIONAL_METADATA_ERRORS as exc:
            logger.debug("Response metadata context unavailable: %s", exc)
        return None


def forget_response_metadata(url):
    capture = _CAPTURE.get()
    if capture is not None and capture.url == url:
        capture._candidate = None


def observe_response_metadata(url, response, session, *, stream):
    capture = _CAPTURE.get()
    if capture is None or capture.url != url:
        return
    capture._candidate = None
    try:
        # Only completed native identity GETs with exact URL/query identity.
        # Streaming/browser/cloudscraper/redirect/conditional/partial results
        # keep the existing independent metadata probe.
        if stream or response.status_code != 200 or response.url != url:
            return
        request = response.request
        if request is None or request.method != "GET" or request.url != url:
            return
        if any(name in request.headers for name in ("Range", "If-None-Match", "If-Modified-Since")):
            return
        headers = response.headers
        if (headers.get("Content-Encoding", "").strip().lower() not in {"", "identity"}
                or headers.get("Content-Range") or headers.get("Vary") or headers.get("Set-Cookie")
                or "no-store" in headers.get("Cache-Control", "").lower()):
            return
        body = getattr(response, "_content", None)
        if not getattr(response, "_content_consumed", False) or not isinstance(body, bytes):
            return
        length = decoded_response_content_length(response)
        if length is not None and length != len(body):
            return
        values = {"etag": headers.get("ETag", ""), "last_modified": headers.get("Last-Modified", ""),
                  "content_length": str(length) if length is not None else ""}
        if not any(values.values()) or any(not isinstance(v, str) or len(v) > 4096 for v in values.values()):
            return
        context = capture.context(url)
        initial = capture._initial
        if (initial is not None and context[0] is session and context[0] is initial[0]
                and context[1] == initial[1]):
            capture._candidate = (values, context)
    except _OPTIONAL_METADATA_ERRORS as exc:
        # Header collection is auxiliary; transports and downloads retain their
        # existing exception and response-ownership semantics.
        logger.debug("Response metadata capture unavailable: %s", exc)
