"""Credits/about GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = ("_show_credits_panel",)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

_show_credits_panel = PANEL_METHODS["_show_credits_panel"]

__all__ = ["PANEL_METHODS", "PANEL_METHOD_NAMES", "_show_credits_panel"]
