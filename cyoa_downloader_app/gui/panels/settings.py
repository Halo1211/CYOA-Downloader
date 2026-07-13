"""Settings, toggle, and settings import/export GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_settings_maintenance_panel",
    "_toggles_panel",
    "_export_settings_dialog",
    "_import_settings_dialog",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

globals().update(PANEL_METHODS)

__all__ = ["PANEL_METHOD_NAMES", "PANEL_METHODS", *PANEL_METHOD_NAMES]
