"""Process-level proxy state helpers.

Phase 64 removes this module's direct dependency on ``legacy.py`` for proxy
state.  ``runtime.state`` is the owner; setters mirror to legacy only when the
facade is already loaded.
"""

from __future__ import annotations

import os
from typing import Dict, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

from requests.utils import should_bypass_proxies

from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy


def _get_active_proxy() -> Optional[str]:
    """Return currently configured proxy URL, honoring disabled/manual/env modes."""
    if state._proxy_mode == "disabled":
        return None
    if state._active_proxy:
        return state._active_proxy
    if state._proxy_mode == "manual":
        return state._proxy_https or state._proxy_http
    if state._proxy_mode == "inherit_env":
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            val = os.environ.get(key, "")
            if val:
                return val
    return None


_SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}


def _normalize_proxy_url(value: Optional[str]) -> Optional[str]:
    """Validate a proxy URL without exposing embedded credentials in logs."""
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in _SUPPORTED_PROXY_SCHEMES or not parsed.hostname:
        raise ValueError(
            "Proxy must use http://, https://, socks4://, socks5://, or socks5h://"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Proxy port is invalid") from exc
    return text


def _redact_proxy_url(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host + (f":{parsed.port}" if parsed.port is not None else "")
        if parsed.username is not None:
            netloc = "***:***@" + netloc
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "[configured]"


def _get_active_proxies() -> Dict[str, str]:
    """Return the requests-compatible proxy mapping for the active profile."""
    if state._proxy_mode == "disabled":
        return {}
    if state._proxy_mode == "manual":
        common = state._active_proxy
        mapping: Dict[str, str] = {}
        if state._proxy_http or common:
            mapping["http"] = str(state._proxy_http or common)
        if state._proxy_https or common:
            mapping["https"] = str(state._proxy_https or common)
        return mapping
    # Environment proxies are consumed by requests via trust_env. Returning a
    # summary here keeps callers such as FlareSolverr compatible.
    proxy = _get_active_proxy()
    return {"http": proxy, "https": proxy} if proxy else {}


def _should_bypass_manual_proxy(url: str) -> bool:
    """Return whether the configured manual proxy bypass matches *url*.

    ``requests`` only applies ``NO_PROXY`` automatically when environment
    proxy discovery is enabled. Manual profiles deliberately disable that
    discovery, so a ``no_proxy`` entry placed in ``Session.proxies`` was
    ignored. Keep bypass evaluation explicit and shared by Requests, browser,
    DoH, and FlareSolverr transports.
    """
    if state._proxy_mode != "manual" or not state._proxy_no_proxy:
        return False
    try:
        return bool(should_bypass_proxies(str(url or ""), no_proxy=state._proxy_no_proxy))
    except (TypeError, ValueError):
        return False


def _get_browser_proxy_config(url: str) -> Optional[Dict[str, str]]:
    """Return a Playwright proxy profile matching the manual request route.

    An empty mapping means direct/system browser routing. ``None`` means the
    selected proxy transport cannot be represented safely by Playwright and a
    browser fallback must not bypass it.
    """
    if state._proxy_mode != "manual":
        return {}
    if _should_bypass_manual_proxy(url):
        return {}
    target_scheme = urlsplit(str(url or "")).scheme.lower() or "https"
    proxy_url = _get_active_proxies().get(target_scheme)
    if not proxy_url:
        return {}
    parsed = urlsplit(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "socks5", "socks5h"}:
        return None
    browser_scheme = "socks5" if scheme == "socks5h" else scheme
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    server = f"{browser_scheme}://{host}"
    if parsed.port is not None:
        server += f":{parsed.port}"
    config = {"server": server}
    if parsed.username is not None:
        config["username"] = unquote(parsed.username)
    if parsed.password is not None:
        config["password"] = unquote(parsed.password)
    if state._proxy_no_proxy:
        config["bypass"] = state._proxy_no_proxy
    return config


def _set_proxy_config(
    *,
    mode: str = "inherit_env",
    proxy: Optional[str] = None,
    http_proxy: Optional[str] = None,
    https_proxy: Optional[str] = None,
    no_proxy: str = "localhost,127.0.0.1,::1",
) -> None:
    """Apply an advanced proxy profile and rebuild pooled sessions."""
    normalized_mode = str(mode or "inherit_env").strip().lower().replace("-", "_")
    if normalized_mode not in {"inherit_env", "manual", "disabled"}:
        raise ValueError("Unknown proxy mode")
    common = _normalize_proxy_url(proxy) if normalized_mode == "manual" else None
    http_value = _normalize_proxy_url(http_proxy) if normalized_mode == "manual" else None
    https_value = _normalize_proxy_url(https_proxy) if normalized_mode == "manual" else None
    if normalized_mode == "manual" and not any((common, http_value, https_value)):
        raise ValueError("Manual proxy mode requires at least one proxy URL")
    bypass = ",".join(part.strip() for part in str(no_proxy or "").split(",") if part.strip())
    changed = (
        normalized_mode != state._proxy_mode
        or common != state._active_proxy
        or http_value != state._proxy_http
        or https_value != state._proxy_https
        or bypass != state._proxy_no_proxy
    )
    state._proxy_mode = normalized_mode
    state._active_proxy = common
    state._proxy_http = http_value
    state._proxy_https = https_value
    state._proxy_no_proxy = bypass
    for name in (
        "_proxy_mode", "_active_proxy", "_proxy_http", "_proxy_https", "_proxy_no_proxy",
    ):
        mirror_to_legacy(name, getattr(state, name))
    if changed:
        try:
            from .sessions import _v465_reset_shared_sessions
            _v465_reset_shared_sessions()
        except Exception as exc:
            logger.debug("Shared-session reset after proxy change failed: %s", exc)
    if changed:
        if normalized_mode == "manual":
            rendered = {
                key: _redact_proxy_url(value)
                for key, value in _get_active_proxies().items()
            }
            logger.info("Manual proxy profile active: %s", rendered)
        elif normalized_mode == "inherit_env":
            logger.info("Proxy mode: inherit environment")
        else:
            logger.info("Proxy disabled, including environment proxies")


def _set_active_proxy(url: Optional[str], *, mode: Optional[str] = None) -> None:
    """Set global proxy. mode=disabled disables env proxy inheritance too."""
    if mode is None:
        new_mode = "manual" if (url and str(url).strip()) else "disabled"
    else:
        new_mode = str(mode or "inherit_env").strip().lower().replace("-", "_")
        if new_mode not in {"inherit_env", "manual", "disabled"}:
            new_mode = "inherit_env"
    _set_proxy_config(mode=new_mode, proxy=url)


__all__ = [
    "_get_active_proxy", "_get_active_proxies", "_set_active_proxy",
    "_set_proxy_config", "_normalize_proxy_url", "_redact_proxy_url",
    "_get_browser_proxy_config", "_should_bypass_manual_proxy",
]
