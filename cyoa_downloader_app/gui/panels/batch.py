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

globals().update(PANEL_METHODS)

__all__ = ["PANEL_METHOD_NAMES", "PANEL_METHODS", *PANEL_METHOD_NAMES]
