"""Format and feature-guide GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_show_format_guide",
    "_show_feature_guide",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

globals().update(PANEL_METHODS)

__all__ = ["PANEL_METHOD_NAMES", "PANEL_METHODS", *PANEL_METHOD_NAMES]
