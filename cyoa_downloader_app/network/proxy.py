"""Process-level proxy state helpers.

Phase 64 removes this module's direct dependency on ``legacy.py`` for proxy
state.  ``runtime.state`` is the owner; setters mirror to legacy only when the
facade is already loaded.
"""

from __future__ import annotations

import os
from typing import Optional

from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy


def _get_active_proxy() -> Optional[str]:
    """Return currently configured proxy URL, honoring disabled/manual/env modes."""
    if state._proxy_mode == "disabled":
        return None
    if state._active_proxy:
        return state._active_proxy
    if state._proxy_mode == "inherit_env":
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            val = os.environ.get(key, "")
            if val:
                return val
    return None


def _set_active_proxy(url: Optional[str], *, mode: Optional[str] = None) -> None:
    """Set global proxy. mode=disabled disables env proxy inheritance too."""
    if mode is None:
        new_mode = "manual" if (url and str(url).strip()) else "disabled"
    else:
        new_mode = str(mode or "inherit_env").strip().lower().replace("-", "_")
        if new_mode not in {"inherit_env", "manual", "disabled"}:
            new_mode = "inherit_env"
    new_proxy = str(url).strip() if (url and new_mode == "manual") else None
    if new_proxy == state._active_proxy and new_mode == state._proxy_mode:
        return
    state._active_proxy = new_proxy
    state._proxy_mode = new_mode
    mirror_to_legacy("_active_proxy", state._active_proxy)
    mirror_to_legacy("_proxy_mode", state._proxy_mode)
    try:
        from .sessions import _v465_reset_shared_sessions
        _v465_reset_shared_sessions()
    except Exception as exc:
        logger.debug("Shared-session reset after proxy change failed: %s", exc)
    if state._proxy_mode == "manual" and state._active_proxy:
        logger.info(f"Proxy set: {state._active_proxy}")
    elif state._proxy_mode == "inherit_env":
        logger.info("Proxy mode: inherit environment")
    else:
        logger.info("Proxy disabled, including environment proxies")
