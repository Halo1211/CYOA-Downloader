"""Diagnostics / retry / result GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_diagnostics_panel",
    "_retry_youtube_audio",
    "_retry_failed_images",
    "_retry_failed",
    "_show_results",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

globals().update(PANEL_METHODS)

__all__ = ["PANEL_METHOD_NAMES", "PANEL_METHODS", *PANEL_METHOD_NAMES]
