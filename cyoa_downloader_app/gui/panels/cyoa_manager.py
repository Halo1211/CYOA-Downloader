"""CYOA Manager integration GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_cyoa_manager_panel",
    "_import_from_cyoa_manager_panel",
    "_show_cookie_guide",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

_cyoa_manager_panel = PANEL_METHODS["_cyoa_manager_panel"]
_import_from_cyoa_manager_panel = PANEL_METHODS["_import_from_cyoa_manager_panel"]
_show_cookie_guide = PANEL_METHODS["_show_cookie_guide"]

__all__ = [
    "PANEL_METHODS",
    "PANEL_METHOD_NAMES",
    "_cyoa_manager_panel",
    "_import_from_cyoa_manager_panel",
    "_show_cookie_guide",
]
