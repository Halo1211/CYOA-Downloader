"""Final public fetch_response wrapper."""

from __future__ import annotations

import requests

from ..core.atomic_io import decoded_response_content_length
from ._bridge import legacy


def fetch_response(
    url: str,
    extra_headers: dict | None = None,
    timeout: int = 20,
    as_bytes: bool = False,
    quiet: bool = False,
    return_error_response: bool = False,
    stream: bool = False,
) -> requests.Response | None:
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
    try:
        l._raise_if_cancelled()
        if response is not None:
            length = decoded_response_content_length(response)
            if as_bytes:
                l.validate_response_content_length(response, len(response.content))
            l._emit_progress_event(
                "response_meta",
                url=str(getattr(response, "url", None) or url),
                status=int(getattr(response, "status_code", 0) or 0),
                content_length=length,
            )
    except BaseException:
        # When cancellation or body validation fires after the blocking
        # request returns, ownership never reaches the caller. Close here so
        # the pooled connection is not leaked.
        if response is not None:
            try:
                response.close()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
                l.logger.debug("Could not close abandoned response for %s: %s", url, exc)
        raise
    return response
