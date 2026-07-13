"""Base network fetch implementation formerly embedded in ``legacy.py``.

The public ``fetch_response`` wrapper still lives in ``network.fetch`` and adds
cancellation/progress metadata. This module owns the historical request logic
used by that wrapper.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from ._bridge import legacy
from .cloudflare import (
    is_cloudflare_challenge,
    _normalize_cloudflare_mode,
    fetch_via_flaresolverr,
)
from .sessions import _get_shared_session
from .throttle import _domain_throttle
from ..download.headers import get_headers_for_url
from ..integrations.ai_core import _host_resolves_internal, _ssrf_block_cross_origin


def base_fetch_response(
    url: str,
    extra_headers: Optional[Dict] = None,
    timeout: int = 20,
    as_bytes: bool = False,
    quiet: bool = False,
    return_error_response: bool = False,
    stream: bool = False,
) -> Optional[requests.Response]:
    """
    Fetch a URL with automatic fallbacks:
    - Cloudflare mode: off/auto/cloudscraper/flaresolverr
    - Auto mode: normal request → cloudscraper → FlareSolverr when a challenge is detected
    - TLS certificates are always verified
    - Domain rate throttle (300ms/domain)
    - Friendly error messages for common connection issues
    """
    l = legacy()
    # ── v7.5.6 hardening: only http/https may reach the network stack.
    # Rejects file://, ftp://, data:, javascript:, chrome:// etc. that can
    # arrive via crafted project JSON, AI output, or malformed HTML before
    # they hit requests (which would raise a noisy InvalidSchema, or worse
    # for custom adapters). Scheme-relative "//host/x" is allowed upstream
    # because callers urljoin it first.
    try:
        _scheme = urlparse(url).scheme.lower()
    except Exception:
        _scheme = ""
    if _scheme not in ("http", "https"):
        if not quiet:
            l.logger.warning(f"Blocked non-http(s) URL scheme '{_scheme or '?'}': {url[:120]}")
        return None
    _domain_throttle(url)
    headers = get_headers_for_url(url) or {"User-Agent": "Mozilla/5.0"}
    if extra_headers:
        headers.update(extra_headers)

    def _origin(value: str) -> Tuple[str, str, Optional[int]]:
        parsed = urlparse(value)
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme.lower() == "https" else 80
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port

    def _strip_cross_origin_secrets(values: Dict) -> Dict:
        sanitized = dict(values)
        sensitive = {"authorization", "proxy-authorization", "cookie", "host"}
        for name in list(sanitized):
            if str(name).lower() in sensitive:
                sanitized.pop(name, None)
        return sanitized

    def _do_request(*, use_cf_session: bool = False, verify_ssl: bool = True):
        try:
            session = _get_shared_session(use_cf=bool(use_cf_session))
            request_url = url
            request_headers = dict(headers)
            initial_host = urlparse(url).hostname or ""
            initial_internal = _host_resolves_internal(initial_host)
            for redirect_count in range(11):
                r = session.get(
                    request_url, headers=request_headers, timeout=timeout,
                    allow_redirects=False, verify=verify_ssl, stream=stream,
                )
                location = r.headers.get("Location", "")
                if r.status_code not in {301, 302, 303, 307, 308} or not location:
                    break
                next_url = urljoin(request_url, location)
                try:
                    next_parsed = urlparse(next_url)
                    if next_parsed.scheme.lower() not in {"http", "https"} or not next_parsed.hostname:
                        raise ValueError("redirect target is not an HTTP(S) URL")
                    _ = next_parsed.port  # validates malformed/non-numeric ports
                except (TypeError, ValueError):
                    r.close()
                    l.logger.warning(f"Blocked invalid redirect target: {next_url}")
                    return None
                # Cross-origin private redirects are blocked. Also block a
                # same-origin target that newly resolves private when the
                # explicitly requested origin did not (DNS-rebinding guard).
                if _ssrf_block_cross_origin(next_url, url) or (
                    not initial_internal and _ssrf_block_cross_origin(next_url, "")
                ):
                    r.close()
                    l.logger.warning(f"Blocked redirect to internal host: {next_url}")
                    return None
                if _origin(next_url) != _origin(request_url):
                    request_headers = _strip_cross_origin_secrets(request_headers)
                r.close()
                request_url = next_url
            else:
                raise requests.TooManyRedirects(f"Too many redirects: {url}")
            if is_cloudflare_challenge(r):
                return "CF_CHALLENGE"
            if return_error_response and r.status_code in {429, 500, 502, 503, 504}:
                return r
            r.raise_for_status()
            if as_bytes:
                _ = r.content
            return r
        except requests.exceptions.SSLError:
            return "SSL_ERROR"
        except requests.exceptions.ConnectionError as e:
            err = str(e).lower()
            if "connection reset" in err or "econnreset" in err:
                l.logger.warning(f"Connection reset oleh server: {url} — coba lagi nanti")
            elif "name or service not known" in err or "nodename nor servname" in err:
                l.logger.error(f"Domain tidak ditemukan (DNS): {url}")
            else:
                l.logger.error(f"Connection error: {url} — {e}")
            return None
        except requests.exceptions.Timeout:
            l.logger.warning(f"Timeout ({timeout}s): {url}")
            return None
        except requests.RequestException as e:
            # A Cloudflare-protected page often returns 403/503 without a parseable challenge body.
            # Do not classify plain HTTP 429 as Cloudflare; callers may need Retry-After.
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in {403, 503}:
                return "CF_CHALLENGE"
            if quiet:
                l.logger.debug(f"Probe miss: {url} — {e}")
            else:
                l.logger.error(f"Error: {url} — {e}")
            return None

    cf_mode = _normalize_cloudflare_mode(l._CLOUDFLARE_MODE)
    attempts: List[Tuple[str, bool]] = []
    if cf_mode == "cloudscraper":
        attempts = [("cloudscraper", True), ("normal", False)]
    elif cf_mode == "flaresolverr":
        attempts = [("flaresolverr", False), ("normal", False)]
    else:
        attempts = [("normal", False)]

    result = None
    challenge_seen = False
    ssl_error = False

    for label, use_cf_session in attempts:
        if label == "flaresolverr":
            result = fetch_via_flaresolverr(url, extra_headers=headers, timeout=timeout)
        else:
            result = _do_request(use_cf_session=use_cf_session, verify_ssl=True)
        if result == "CF_CHALLENGE":
            challenge_seen = True
            l.logger.warning(f"[Cloudflare] Challenge detected: {url}")
            continue
        if result == "SSL_ERROR":
            ssl_error = True
            break
        if result is not None:
            l.logger.info(f"Downloaded: {url}" + (f" via {label}" if label != "normal" else ""))
            return result

    if ssl_error:
        l.logger.error(f"TLS certificate verification failed: {url}")

    # Auto fallback chain: only escalate when a Cloudflare challenge is actually detected.
    if cf_mode == "auto" and challenge_seen:
        l.logger.info("[Cloudflare] Auto mode: trying cloudscraper fallback…")
        result = _do_request(use_cf_session=True, verify_ssl=True)
        if result and result not in {"SSL_ERROR", "CF_CHALLENGE"}:
            l.logger.info(f"Downloaded: {url} via cloudscraper")
            return result
        l.logger.info("[Cloudflare] Auto mode: trying FlareSolverr fallback…")
        result = fetch_via_flaresolverr(url, extra_headers=headers, timeout=timeout)
        if result is not None:
            return result

    if challenge_seen:
        l.logger.warning(
            f"Cloudflare challenge unresolved: {url}\n"
            f"  GUI: set Cloudflare Mode to Auto or FlareSolverr.\n"
            f"  CLI: use --cloudflare auto or --cloudflare flaresolverr."
        )
    return None

__all__ = ["base_fetch_response"]
