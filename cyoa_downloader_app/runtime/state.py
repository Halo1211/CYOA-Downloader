"""Mutable runtime state preserved for legacy compatibility.

Phase 62 moves the remaining single-file global state declarations out of
``legacy.py`` while keeping the same names re-exported by the facade.  These
objects intentionally mirror the historical module-level state; later phases
can replace the direct globals with explicit context objects.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import socket as _socket
import threading
import threading as _threading
import time as _time
import random as _random

from ..app_info import DEFAULT_WAIT_TIME

wait_time: int = DEFAULT_WAIT_TIME
_RUN_DOWNLOAD_LOCK = threading.RLock()
_LAST_PREVIEW_FOLDER: Optional[str] = None

# Cloudflare / downloader feature toggles.
use_cloudscraper: bool = False
_ytdlp_enabled: bool = True
_HTTP2_ENABLED: bool = False
_DEEP_SCAN_ENABLED: bool = True
_SELENIUM_ENABLED: bool = True
_SERVE_ENABLED: bool = True
_CHEAT_ENABLED: bool = True
_ITCH_ENABLED: bool = False

# Cloudflare / FlareSolverr runtime config.
_CLOUDFLARE_MODE: str = "auto"
_FLARESOLVERR_URL: str = "http://localhost:8191/v1"
_FLARESOLVERR_SESSION_POLICY: str = "reuse-domain"
_FLARESOLVERR_TIMEOUT: int = 60
_FLARESOLVERR_WAIT_AFTER: int = 3
_FLARESOLVERR_PROXY_MODE: str = "inherit"
_FLARESOLVERR_SESSIONS: Dict[str, str] = {}
_FLARESOLVERR_LOCK = threading.Lock()

# Bandwidth throttle / speed tracking state.
_bandwidth_limit_kbps: float = 0.0
_bw_lock = _threading.Lock()
_bw_last_time: float = 0.0
_bw_bytes_this_window: int = 0
_gui_speed_cb: Optional[Any] = None

# Shared HTTP sessions.
_v465_session_init_lock = threading.RLock()
_shared_session = None
_shared_session_cf = None

# Domain rate limiter / exponential backoff.
_domain_last_request: Dict[str, float] = {}
_domain_lock = _threading.Lock()
_domain_min_interval: float = 0.3
_domain_backoff: Dict[str, float] = {}
_domain_fail_count: Dict[str, int] = {}
_domain_backoff_lock = _threading.Lock()
_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 300.0
_BACKOFF_JITTER = 0.25

# yt-dlp GUI progress callback.
_ytdlp_gui_progress_cb = None

# Proxy config.
_active_proxy: Optional[str] = None
_proxy_mode: str = "inherit_env"

# DNS config.
_active_dns: Optional[str] = None
_orig_getaddrinfo = _socket.getaddrinfo
DNS_PRESETS: Dict[str, str] = {
    "System (default)": "",
    "BebasDNS Default (DoH)": "https://dns.bebasid.com/dns-query",
    "BebasDNS Security (DoH)": "https://security.dns.bebasid.com/dns-query",
    "BebasDNS Unfiltered (DoH)": "https://unfiltered.dns.bebasid.com/dns-query",
    "BebasDNS Family (DoH)": "https://family.dns.bebasid.com/dns-query",
    "Cloudflare 1.1.1.1": "1.1.1.1",
    "Cloudflare 1.0.0.1": "1.0.0.1",
    "Google 8.8.8.8": "8.8.8.8",
    "Google 8.8.4.4": "8.8.4.4",
    "Quad9 9.9.9.9": "9.9.9.9",
    "OpenDNS 208.67.222": "208.67.222.222",
    "AdGuard 94.140.14": "94.140.14.14",
    "Custom…": "__custom__",
}
BEBASDNS_DOH_VARIANTS: Dict[str, str] = {
    "default": "https://dns.bebasid.com/dns-query",
    "security": "https://security.dns.bebasid.com/dns-query",
    "unfiltered": "https://unfiltered.dns.bebasid.com/dns-query",
    "family": "https://family.dns.bebasid.com/dns-query",
}
_dns_bypass_local = _threading.local()
_dns_cache: Dict[Tuple[str, str, int], Tuple[float, str]] = {}
_DNS_CACHE_TTL_SECONDS = 300

__all__ = [name for name in globals() if not (name.startswith("__") and name.endswith("__"))]
