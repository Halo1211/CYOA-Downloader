"""Preview-token lifecycle state for local preview servers."""

from __future__ import annotations

import secrets as _secrets
import threading

_PREVIEW_TOKEN_LOCK = threading.Lock()
_PREVIEW_SESSION_TOKEN: str = ""


def _new_preview_token() -> str:
    """Mint and store a new preview session token. Returns the token."""
    global _PREVIEW_SESSION_TOKEN
    tok = _secrets.token_urlsafe(9)
    with _PREVIEW_TOKEN_LOCK:
        _PREVIEW_SESSION_TOKEN = tok
    return tok


def _current_preview_token() -> str:
    with _PREVIEW_TOKEN_LOCK:
        return _PREVIEW_SESSION_TOKEN


def _clear_preview_token() -> None:
    """Invalidate the active preview session (called on server stop)."""
    global _PREVIEW_SESSION_TOKEN
    with _PREVIEW_TOKEN_LOCK:
        _PREVIEW_SESSION_TOKEN = ""


def _preview_token_valid(tok: str) -> bool:
    """True only if ``tok`` matches the currently active preview session.

    The parameter name intentionally matches the original single-file API so
    keyword calls like ``_preview_token_valid(tok=...)`` keep working.
    """
    cur = _current_preview_token()
    return bool(cur) and tok == cur


__all__ = [
    "_PREVIEW_TOKEN_LOCK", "_PREVIEW_SESSION_TOKEN", "_new_preview_token",
    "_current_preview_token", "_clear_preview_token", "_preview_token_valid",
]
