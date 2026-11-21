"""Batch queue/import/export GUI panel methods."""

from __future__ import annotations

from ._bridge import method_map

PANEL_METHOD_NAMES = (
    "_make_queue_row",
    "_bind_drag",
    "_swap_rows",
    "_remove_row",
    "_update_queue_count",
    "_add_to_queue",
    "_remove",
    "_clear_queue",
    "_import_list",
    "_preview_queue",
    "_batch_export_panel",
    "_batch_update_panel",
    "_add_url_to_queue",
    "_remove_urls_from_queue",
)

PANEL_METHODS = method_map(PANEL_METHOD_NAMES)

_make_queue_row = PANEL_METHODS["_make_queue_row"]
_bind_drag = PANEL_METHODS["_bind_drag"]
_swap_rows = PANEL_METHODS["_swap_rows"]
_remove_row = PANEL_METHODS["_remove_row"]
_update_queue_count = PANEL_METHODS["_update_queue_count"]
_add_to_queue = PANEL_METHODS["_add_to_queue"]
_remove = PANEL_METHODS["_remove"]
_clear_queue = PANEL_METHODS["_clear_queue"]
_import_list = PANEL_METHODS["_import_list"]
_preview_queue = PANEL_METHODS["_preview_queue"]
_batch_export_panel = PANEL_METHODS["_batch_export_panel"]
_batch_update_panel = PANEL_METHODS["_batch_update_panel"]
_add_url_to_queue = PANEL_METHODS["_add_url_to_queue"]
_remove_urls_from_queue = PANEL_METHODS["_remove_urls_from_queue"]

__all__ = [
    "PANEL_METHODS",
    "PANEL_METHOD_NAMES",
    "_add_to_queue",
    "_add_url_to_queue",
    "_batch_export_panel",
    "_batch_update_panel",
    "_bind_drag",
    "_clear_queue",
    "_import_list",
    "_make_queue_row",
    "_preview_queue",
    "_remove",
    "_remove_row",
    "_remove_urls_from_queue",
    "_swap_rows",
    "_update_queue_count",
]
