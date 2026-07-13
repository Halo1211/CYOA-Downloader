"""Final public fetch_response wrapper."""

from __future__ import annotations

from typing import Dict, Optional

import requests

from ._bridge import legacy


def fetch_response(
    url: str,
    extra_headers: Optional[Dict] = None,
    timeout: int = 20,
    as_bytes: bool = False,
    quiet: bool = False,
    return_error_response: bool = False,
    stream: bool = False,
) -> Optional[requests.Response]:
    """Fetch URL via the legacy v46 implementation plus cancellation/metadata hooks."""
    l = legacy()
    l._raise_if_cancelled()
    response = l._v46_fetch_response_legacy(
        url,
        extra_headers=extra_headers,
        timeout=timeout,
        as_bytes=as_bytes,
        quiet=quiet,
        return_error_response=return_error_response,
        stream=stream,
    )
    l._raise_if_cancelled()
    if response is not None:
        raw_length = response.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length not in (None, "") else None
        except (TypeError, ValueError):
            length = None
        if as_bytes:
            l.validate_response_content_length(response, len(response.content))
        l._emit_progress_event(
            "response_meta",
            url=str(getattr(response, "url", None) or url),
            status=int(getattr(response, "status_code", 0) or 0),
            content_length=length,
        )
    return response
