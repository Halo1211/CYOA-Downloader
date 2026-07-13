"""Legacy compatibility exports.

This module intentionally re-exports every public and legacy-private symbol from
`cyoa_downloader_app.runtime.surface` except Python dunder names. Existing tests, scripts,
and GUI/CLI entry points can continue importing from `cyoa_downloader` while the
large implementation is moved into domain modules incrementally.
"""

from .runtime import surface as _legacy

__all__ = [name for name in vars(_legacy) if not (name.startswith("__") and name.endswith("__"))]

globals().update({name: getattr(_legacy, name) for name in __all__})


def __getattr__(name):
    return getattr(_legacy, name)

# Phase 7 GUI facade aliases. Keep these names identical to the new domain
# modules while all non-overridden legacy names continue to be re-exported
# above for compatibility.
from .gui.app import CYOADownloaderGUI, launch_gui, _gui_exists  # noqa: E402,F401
from .gui.widgets import GUILogHandler, _v25_safe_after, _v25_safe_after_widget, _v27_safe_after  # noqa: E402,F401
from .gui.preview_server import (  # noqa: E402,F401
    _set_serve_enabled, _new_preview_token, _current_preview_token,
    _clear_preview_token, _preview_token_valid, userscript_integration_report,
)
from .gui.theme import _normalize_theme_mode, _normalize_accent_color, _system_prefers_dark, _resolve_theme_is_dark, _v465_apply_theme  # noqa: E402,F401
from .gui.composition import (  # noqa: E402,F401
    PATCH_ORDER, apply_gui_patches, applied_patch_order, expected_patch_surface,
    apply_v24, apply_v25, apply_v27, apply_v46, apply_v462, apply_v463, apply_v465, apply_v466,
)


# Phase 9 GUI panel facade aliases. Panel modules expose the already-patched
# compatibility-surface method objects, then attach them centrally to the GUI class.
from .gui.panels import (  # noqa: E402,F401
    PANEL_BIND_ORDER, attach_panel_methods, bound_panel_methods,
    panel_method_names, panel_method_map,
)
from .gui.panels import ai as gui_panel_ai  # noqa: E402,F401
from .gui.panels import batch as gui_panel_batch  # noqa: E402,F401
from .gui.panels import cache as gui_panel_cache  # noqa: E402,F401
from .gui.panels import cloudflare as gui_panel_cloudflare  # noqa: E402,F401
from .gui.panels import credits as gui_panel_credits  # noqa: E402,F401
from .gui.panels import cyoa_manager as gui_panel_cyoa_manager  # noqa: E402,F401
from .gui.panels import diagnostics as gui_panel_diagnostics  # noqa: E402,F401
from .gui.panels import guide as gui_panel_guide  # noqa: E402,F401
from .gui.panels import offline_viewers as gui_panel_offline_viewers  # noqa: E402,F401
from .gui.panels import settings as gui_panel_settings  # noqa: E402,F401
from .gui.panels import updates as gui_panel_updates  # noqa: E402,F401

for _phase7_name in [
    "CYOADownloaderGUI", "launch_gui", "_gui_exists", "GUILogHandler",
    "_v25_safe_after", "_v25_safe_after_widget", "_v27_safe_after",
    "_set_serve_enabled", "_new_preview_token", "_current_preview_token",
    "_clear_preview_token", "_preview_token_valid", "userscript_integration_report",
    "_normalize_theme_mode", "_normalize_accent_color", "_system_prefers_dark", "_resolve_theme_is_dark", "_v465_apply_theme",
    "PATCH_ORDER", "apply_gui_patches", "applied_patch_order", "expected_patch_surface",
    "apply_v24", "apply_v25", "apply_v27", "apply_v46", "apply_v462", "apply_v463", "apply_v465", "apply_v466",
    "PANEL_BIND_ORDER", "attach_panel_methods", "bound_panel_methods", "panel_method_names", "panel_method_map",
    "gui_panel_ai", "gui_panel_batch", "gui_panel_cache", "gui_panel_cloudflare", "gui_panel_credits",
    "gui_panel_cyoa_manager", "gui_panel_diagnostics", "gui_panel_guide", "gui_panel_offline_viewers",
    "gui_panel_settings", "gui_panel_updates",
]:
    if _phase7_name not in __all__:
        __all__.append(_phase7_name)
