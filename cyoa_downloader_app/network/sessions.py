"""Requests session construction and shared-session lifecycle.

Phase 64 makes ``runtime.state`` the primary owner for shared HTTP sessions.
Legacy is mirrored only for compatibility so domain modules do not need to use
legacy.py as the live state store.
"""

from __future__ import annotations

import weakref

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.cancellation import _cancel_aware_sleep, _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy
from .budget import HTTP_BUDGET, HTTP_GLOBAL_LIMIT, HTTP_HOST_LIMIT
from .proxy import _get_active_proxies
from .retry_policy import MEDIA_STATUS_RETRY_CODES, status_retries_owned_by_caller


class _CancellableRetry(Retry):
    """Keep urllib3 retry/backoff semantics while allowing GUI cancellation."""

    def is_retry(self, method, status_code, has_retry_after=False):
        if status_code in MEDIA_STATUS_RETRY_CODES and status_retries_owned_by_caller():
            return False
        return super().is_retry(method, status_code, has_retry_after)

    def sleep(self, response=None) -> None:
        _raise_if_cancelled()
        if self.respect_retry_after_header and response:
            retry_after = self.get_retry_after(response)
            if retry_after:
                _cancel_aware_sleep(retry_after)
                return
        backoff = self.get_backoff_time()
        if backoff > 0:
            _cancel_aware_sleep(backoff)


def _abandon_response(raw, lease):
    """Last resort for an unclosed response; explicit close remains preferred."""
    try:
        if raw is not None:
            raw.close()
            release = getattr(raw, "release_conn", None)
            if release is not None:
                release()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Abandoned HTTP response cleanup failed: %s", exc)
    finally:
        lease.release()


def _lease_response(response, lease):
    original_close, original_iter = response.close, response.iter_content
    finalizer = weakref.finalize(response, _abandon_response, response.raw, lease)

    def close():
        try:
            original_close()
        finally:
            lease.release()
            finalizer.detach()

    def iter_content(chunk_size=1, decode_unicode=False):
        chunks = original_iter(chunk_size=chunk_size, decode_unicode=decode_unicode)
        def consume():
            try:
                yield from chunks
            finally:
                # Includes body/decompression errors and generator.close().
                close()
        return consume()

    response.close = close
    response.iter_content = iter_content
    return response


class _BudgetHTTPAdapter(HTTPAdapter):
    def send(self, request, *args, **kwargs):
        lease = HTTP_BUDGET.acquire(request.url)
        try:
            response = super().send(request, *args, **kwargs)
            return _lease_response(response, lease)
        except BaseException:
            lease.release()
            raise


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
    retry_strategy = _CancellableRetry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = _BudgetHTTPAdapter(max_retries=retry_strategy, pool_connections=HTTP_GLOBAL_LIMIT,
                                 pool_maxsize=HTTP_HOST_LIMIT)
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

    # Requests discovers environment proxies per target URL, including
    # NO_PROXY and scheme overrides. Preseeding them in Session.proxies
    # defeats bypass and applies HTTPS_PROXY to plain HTTP targets.
    proxy_mapping = _get_active_proxies() if state._proxy_mode == "manual" else {}
    if proxy_mapping:
        session.proxies.update(proxy_mapping)
        logger.debug("Session using configured proxy profile")
    return session


def _v465_reset_shared_sessions() -> None:
    """Close pooled connections before invalidating shared sessions."""
    with state._v465_session_init_lock:
        old_sessions = (state._shared_session, state._shared_session_cf)
        state._shared_session = None
        state._shared_session_cf = None
        mirror_to_legacy("_shared_session", None)
        mirror_to_legacy("_shared_session_cf", None)
    seen: set[int] = set()
    for session in old_sessions:
        if session is None or id(session) in seen:
            continue
        seen.add(id(session))
        try:
            session.close()
        except DownloadCancelledError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
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
