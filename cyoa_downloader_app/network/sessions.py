"""Requests session construction and shared-session lifecycle.

Phase 64 makes ``runtime.state`` the primary owner for shared HTTP sessions.
Legacy is mirrored only for compatibility so domain modules do not need to use
legacy.py as the live state store.
"""

from __future__ import annotations

from typing import Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy
from .proxy import _get_active_proxy


def create_retry_session(use_cloudscraper: bool = False) -> requests.Session:
    """Create a requests session with retry strategy, proxy, and redirect limits."""
    if use_cloudscraper:
        try:
            import cloudscraper as _cs  # type: ignore
            session = _cs.create_scraper()
        except ImportError:
            logger.warning(
                "cloudscraper requested but not installed; falling back to normal requests. "
                "Install: pip install cloudscraper"
            )
            session = requests.Session()
    else:
        session = requests.Session()

    session.trust_env = (state._proxy_mode == "inherit_env")
    retry_strategy = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.max_redirects = 10
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })

    proxy_url = _get_active_proxy()
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
        logger.debug(f"Session using proxy: {proxy_url}")
    return session


def _v465_reset_shared_sessions() -> None:
    """Close pooled connections before invalidating shared sessions."""
    with state._v465_session_init_lock:
        old_sessions = (state._shared_session, state._shared_session_cf)
        state._shared_session = None
        state._shared_session_cf = None
        mirror_to_legacy("_shared_session", None)
        mirror_to_legacy("_shared_session_cf", None)
    seen: Set[int] = set()
    for session in old_sessions:
        if session is None or id(session) in seen:
            continue
        seen.add(id(session))
        try:
            session.close()
        except Exception as exc:
            logger.debug(f"Shared session close failed: {exc}")


def _get_shared_session(use_cf: bool = False) -> requests.Session:
    """Return the lazily-created shared session without an initialization race."""
    with state._v465_session_init_lock:
        if use_cf:
            if state._shared_session_cf is None:
                state._shared_session_cf = create_retry_session(use_cloudscraper=True)
                mirror_to_legacy("_shared_session_cf", state._shared_session_cf)
            return state._shared_session_cf
        if state._shared_session is None:
            state._shared_session = create_retry_session(use_cloudscraper=False)
            mirror_to_legacy("_shared_session", state._shared_session)
        return state._shared_session
