"""Cloudflare / network toggle GUI panel methods.

These panel methods are exposed from a domain module while preserving the
composed GUI implementations byte-for-byte.
"""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_cloudflare_panel",
    "_on_cloudflare_mode_change",
    "_on_cf_bypass_toggle",
    "_on_http2_toggle",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

_cloudflare_panel = PANEL_METHODS["_cloudflare_panel"]
_on_cloudflare_mode_change = PANEL_METHODS["_on_cloudflare_mode_change"]
_on_cf_bypass_toggle = PANEL_METHODS["_on_cf_bypass_toggle"]
_on_http2_toggle = PANEL_METHODS["_on_http2_toggle"]

__all__ = [
    "PANEL_METHODS",
    "PANEL_METHOD_NAMES",
    "_cloudflare_panel",
    "_on_cf_bypass_toggle",
    "_on_cloudflare_mode_change",
    "_on_http2_toggle",
]
