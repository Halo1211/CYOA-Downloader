"""Offline-viewer management GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = ("_manage_offline_viewers",)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

_manage_offline_viewers = PANEL_METHODS["_manage_offline_viewers"]

__all__ = ["PANEL_METHODS", "PANEL_METHOD_NAMES", "_manage_offline_viewers"]
