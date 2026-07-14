"""Cloudflare / FlareSolverr challenge handling helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from ._bridge import legacy


def is_cloudflare_challenge(response) -> bool:
    """Detect Cloudflare challenge pages, even on 200 responses."""
    if response is None:
        return False
    ct = response.headers.get("Content-Type", "")
    if "text/html" not in ct and "application/json" not in ct:
        return False
    server = response.headers.get("Server", "").lower()
    cf_ray = response.headers.get("CF-RAY", "")
    if not cf_ray and "cloudflare" not in server:
        return False
    text_sample = response.text[:2000] if hasattr(response, "text") else ""
    cf_markers = [
        "cf-browser-verification", "challenge-platform", "jschl_vc",
        "jschl-answer", "cf_clearance", "Checking your browser",
        "Enable JavaScript and cookies", "cf-turnstile", "DDoS protection",
    ]
    return any(m in text_sample for m in cf_markers)


def _normalize_cloudflare_mode(mode: str) -> str:
    m = (mode or "auto").strip().lower().replace(" ", "-").replace("_", "-")
    aliases = {
        "off": "off", "none": "off", "disabled": "off",
        "auto": "auto",
        "cf-bypass": "cloudscraper", "cloudscraper": "cloudscraper", "cloud-scraper": "cloudscraper",
        "flaresolverr": "flaresolverr", "flare-solverr": "flaresolverr", "flaversolverr": "flaresolverr",
    }
    return aliases.get(m, "auto")


def _display_cloudflare_mode(mode: str) -> str:
    m = _normalize_cloudflare_mode(mode)
    return {"off": "Off", "auto": "Auto", "cloudscraper": "cloudscraper", "flaresolverr": "FlareSolverr"}.get(m, "Auto")


def _normalize_cloudflare_priority(priority: str) -> str:
    """Normalize the Auto-mode fallback preference."""
    value = (priority or "flaresolverr_first").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "flaresolverr": "flaresolverr_first",
        "flare_solverr": "flaresolverr_first",
        "flaresolverr_first": "flaresolverr_first",
        "cloudscraper": "cloudscraper_first",
        "cloud_scraper": "cloudscraper_first",
        "cloudscraper_first": "cloudscraper_first",
    }
    return aliases.get(value, "flaresolverr_first")


def _display_cloudflare_priority(priority: str) -> str:
    return {
        "flaresolverr_first": "FlareSolverr first",
        "cloudscraper_first": "cloudscraper first",
    }[_normalize_cloudflare_priority(priority)]


def _normalize_flaresolverr_url(url: str) -> str:
    u = (url or "http://localhost:8191/v1").strip().rstrip("/")
    if not u:
        return "http://localhost:8191/v1"
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"}:
        u = "http://" + u
        parsed = urlparse(u)
    if not parsed.path or parsed.path == "/":
        u = u.rstrip("/") + "/v1"
    elif not parsed.path.rstrip("/").endswith("/v1") and not parsed.path.rstrip("/").endswith("v1"):
        u = u.rstrip("/") + "/v1"
    return u


def _load_cloudflare_settings() -> None:
    """Load persisted Cloudflare/FlareSolverr settings into globals."""
    l = legacy()
    st = l._load_settings()
    _set_cloudflare_config(
        mode=st.get("cloudflare_mode", "auto"),
        priority=st.get("cloudflare_priority", "flaresolverr_first"),
        flaresolverr_url=st.get("flaresolverr_url", "http://localhost:8191/v1"),
        session_policy=st.get("flaresolverr_session_policy", "reuse-domain"),
        timeout=l._coerce_int(st.get("flaresolverr_timeout", 60), 60),
        wait_after=l._coerce_int(st.get("flaresolverr_wait_after", 3), 3),
        proxy_mode=st.get("flaresolverr_proxy_mode", "inherit"),
        persist=False,
    )


def _set_cloudflare_config(
    mode: str = "auto",
    *,
    priority: str = "",
    flaresolverr_url: str = "",
    session_policy: str = "",
    timeout: int = 60,
    wait_after: int = 3,
    proxy_mode: str = "inherit",
    persist: bool = True,
) -> None:
    """Set process-local Cloudflare engine configuration."""
    l = legacy()
    old_mode = getattr(l, "_CLOUDFLARE_MODE", "auto")
    l._CLOUDFLARE_MODE = _normalize_cloudflare_mode(mode)
    l._CLOUDFLARE_PRIORITY = _normalize_cloudflare_priority(
        priority or getattr(l, "_CLOUDFLARE_PRIORITY", "flaresolverr_first")
    )
    l.use_cloudscraper = (l._CLOUDFLARE_MODE == "cloudscraper")
    if flaresolverr_url:
        l._FLARESOLVERR_URL = _normalize_flaresolverr_url(flaresolverr_url)
    l._FLARESOLVERR_SESSION_POLICY = (session_policy or l._FLARESOLVERR_SESSION_POLICY or "reuse-domain").strip().lower()
    if l._FLARESOLVERR_SESSION_POLICY not in {"temporary", "reuse-domain", "manual"}:
        l._FLARESOLVERR_SESSION_POLICY = "reuse-domain"
    try:
        l._FLARESOLVERR_TIMEOUT = max(5, int(timeout or 60))
    except Exception:
        l._FLARESOLVERR_TIMEOUT = 60
    try:
        l._FLARESOLVERR_WAIT_AFTER = max(0, int(wait_after or 0))
    except Exception:
        l._FLARESOLVERR_WAIT_AFTER = 3
    l._FLARESOLVERR_PROXY_MODE = (proxy_mode or "inherit").strip().lower()
    if l._FLARESOLVERR_PROXY_MODE not in {"inherit", "none"}:
        l._FLARESOLVERR_PROXY_MODE = "inherit"

    if old_mode != l._CLOUDFLARE_MODE:
        l._v465_reset_shared_sessions()
    if persist:
        try:
            l._update_settings({
                "cloudflare_mode": l._CLOUDFLARE_MODE,
                "cloudflare_priority": l._CLOUDFLARE_PRIORITY,
                "flaresolverr_url": l._FLARESOLVERR_URL,
                "flaresolverr_session_policy": l._FLARESOLVERR_SESSION_POLICY,
                "flaresolverr_timeout": l._FLARESOLVERR_TIMEOUT,
                "flaresolverr_wait_after": l._FLARESOLVERR_WAIT_AFTER,
                "flaresolverr_proxy_mode": l._FLARESOLVERR_PROXY_MODE,
            })
        except Exception as e:
            l.logger.debug(f"Could not save Cloudflare settings: {e}")


def _flaresolverr_payload_proxy() -> Optional[Dict[str, str]]:
    """Return FlareSolverr proxy object when proxy inheritance is enabled."""
    l = legacy()
    if l._FLARESOLVERR_PROXY_MODE != "inherit":
        return None
    proxy = l._get_active_proxy()
    if not proxy:
        return None
    return {"url": proxy}


def _flaresolverr_post(payload: Dict[str, Any], timeout: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """POST JSON to FlareSolverr /v1. Returns decoded JSON or None."""
    l = legacy()
    api_url = _normalize_flaresolverr_url(l._FLARESOLVERR_URL)
    request_timeout = max((timeout or l._FLARESOLVERR_TIMEOUT) + 10, 20)
    try:
        session = requests.Session()
        try:
            session.trust_env = (getattr(l, "_proxy_mode", "inherit_env") == "inherit_env")
            proxy = l._get_active_proxy()
            if proxy and l._FLARESOLVERR_PROXY_MODE == "inherit":
                parsed = urlparse(api_url)
                if (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
                    session.proxies.update({"http": proxy, "https": proxy})
            r = session.post(api_url, json=payload, timeout=request_timeout)
        finally:
            session.close()
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in {"ok", "success"}:
            l.logger.warning(f"[FlareSolverr] {data.get('message') or data.get('error') or 'request failed'}")
        return data
    except Exception as e:
        l.logger.warning(f"[FlareSolverr] API unavailable at {api_url}: {e}")
        return None


def _flaresolverr_session_key(url: str) -> str:
    host = (urlparse(url).hostname or "default").lower()
    return "cyoa_" + "".join(ch if ch.isalnum() else "_" for ch in host)[:48]


def _flaresolverr_get_session(url: str) -> Optional[str]:
    l = legacy()
    if l._FLARESOLVERR_SESSION_POLICY == "temporary":
        return None
    key = _flaresolverr_session_key(url)
    with l._FLARESOLVERR_LOCK:
        existing = l._FLARESOLVERR_SESSIONS.get(key)
        if existing:
            return existing
        if l._FLARESOLVERR_SESSION_POLICY == "manual":
            return key
        payload: Dict[str, Any] = {"cmd": "sessions.create", "session": key}
        proxy_obj = _flaresolverr_payload_proxy()
        if proxy_obj:
            payload["proxy"] = proxy_obj
        data = _flaresolverr_post(payload, timeout=10)
        if data:
            session_name = data.get("session") or key
            l._FLARESOLVERR_SESSIONS[key] = session_name
            l.logger.info(f"[FlareSolverr] Session ready: {session_name}")
            return session_name
    return None


def flaresolverr_destroy_sessions() -> int:
    """Destroy all sessions created by this app instance."""
    l = legacy()
    destroyed = 0
    with l._FLARESOLVERR_LOCK:
        sessions = list(l._FLARESOLVERR_SESSIONS.values())
        l._FLARESOLVERR_SESSIONS.clear()
    for sess in sessions:
        data = _flaresolverr_post({"cmd": "sessions.destroy", "session": sess}, timeout=10)
        if data:
            destroyed += 1
    if destroyed:
        l.logger.info(f"[FlareSolverr] Destroyed {destroyed} session(s)")
    return destroyed


def flaresolverr_test_connection() -> Tuple[bool, str]:
    """Check whether FlareSolverr API is reachable."""
    data = _flaresolverr_post({"cmd": "sessions.list"}, timeout=10)
    if data:
        sessions = data.get("sessions", [])
        return True, f"Connected. Sessions: {len(sessions) if isinstance(sessions, list) else 'unknown'}"
    return False, "Not reachable. Start FlareSolverr and check the URL."


def _apply_flaresolverr_solution_to_sessions(solution: Dict[str, Any], source_url: str) -> Dict[str, str]:
    """Copy cookies/user-agent from FlareSolverr into requests sessions."""
    l = legacy()
    headers: Dict[str, str] = {}
    ua = solution.get("userAgent") or solution.get("user-agent")
    if ua:
        headers["User-Agent"] = ua
        try:
            l._get_shared_session(False).headers.update({"User-Agent": ua})
            l._get_shared_session(True).headers.update({"User-Agent": ua})
        except Exception as exc:
            l.logger.debug("Ignored recoverable exception in _apply_flaresolverr_solution_to_sessions: %s", exc)
    host = urlparse(source_url).hostname or ""
    for cookie in solution.get("cookies") or []:
        try:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            domain = cookie.get("domain") or host
            path = cookie.get("path") or "/"
            for sess in (l._get_shared_session(False), l._get_shared_session(True)):
                sess.cookies.set(name, value, domain=domain, path=path)
        except Exception as exc:
            l.logger.debug("Ignored recoverable exception in _apply_flaresolverr_solution_to_sessions: %s", exc)
    return headers


def _response_from_flaresolverr_solution(solution: Dict[str, Any], url: str) -> requests.Response:
    """Build a requests.Response-like object from a FlareSolverr solution."""
    l = legacy()
    resp = requests.Response()
    resp.status_code = l._coerce_int(solution.get("status"), 200)
    resp.url = solution.get("url") or url
    resp._content = (solution.get("response") or "").encode("utf-8", errors="replace")
    resp.encoding = "utf-8"
    try:
        headers = solution.get("headers") or {}
        if isinstance(headers, dict):
            resp.headers.update({str(k): str(v) for k, v in headers.items()})
    except Exception:
        pass
    if "Content-Type" not in resp.headers:
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


def fetch_via_flaresolverr(url: str, extra_headers: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> Optional[requests.Response]:
    """Solve/fetch URL through FlareSolverr and return a Response-like object."""
    l = legacy()
    session_name = _flaresolverr_get_session(url)
    payload: Dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": int((timeout or l._FLARESOLVERR_TIMEOUT) * 1000),
    }
    if session_name:
        payload["session"] = session_name
        payload["session_ttl_minutes"] = max(5, int(l._FLARESOLVERR_TIMEOUT // 2 or 30))
    if l._FLARESOLVERR_WAIT_AFTER:
        payload["waitInSeconds"] = int(l._FLARESOLVERR_WAIT_AFTER)
    if extra_headers:
        payload["headers"] = dict(extra_headers)
    proxy_obj = _flaresolverr_payload_proxy()
    if proxy_obj:
        payload["proxy"] = proxy_obj

    l.logger.info(f"[FlareSolverr] Solving/fetching: {url}")
    data = _flaresolverr_post(payload, timeout=(timeout or l._FLARESOLVERR_TIMEOUT))
    if not data:
        return None
    solution = data.get("solution") or {}
    if not isinstance(solution, dict):
        return None
    _apply_flaresolverr_solution_to_sessions(solution, url)
    resp = _response_from_flaresolverr_solution(solution, url)
    if resp.status_code >= 400:
        l.logger.warning(f"[FlareSolverr] HTTP {resp.status_code}: {url}")
        return resp
    l.logger.info(f"[FlareSolverr ✓] {url}")
    return resp
