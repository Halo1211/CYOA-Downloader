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

_diagnostics_panel = PANEL_METHODS["_diagnostics_panel"]
_retry_youtube_audio = PANEL_METHODS["_retry_youtube_audio"]
_retry_failed_images = PANEL_METHODS["_retry_failed_images"]
_retry_failed = PANEL_METHODS["_retry_failed"]
_show_results = PANEL_METHODS["_show_results"]

__all__ = [
    "PANEL_METHODS",
    "PANEL_METHOD_NAMES",
    "_diagnostics_panel",
    "_retry_failed",
    "_retry_failed_images",
    "_retry_youtube_audio",
    "_show_results",
]
