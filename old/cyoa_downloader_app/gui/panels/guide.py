"""Format and feature-guide GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_show_format_guide",
    "_show_feature_guide",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

_show_format_guide = PANEL_METHODS["_show_format_guide"]
_show_feature_guide = PANEL_METHODS["_show_feature_guide"]

__all__ = [
    "PANEL_METHODS",
    "PANEL_METHOD_NAMES",
    "_show_feature_guide",
    "_show_format_guide",
]
