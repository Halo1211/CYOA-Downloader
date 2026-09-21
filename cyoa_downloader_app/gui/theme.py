"""GUI theme compatibility aliases.

Phase 66 removes the legacy-module bridge from the theme facade. Normalization
helpers are owned by config/settings and the runtime theme applier is owned by
``gui.final_behaviors``.
"""

from __future__ import annotations

from ..config.settings import (
    _normalize_accent_color,
    _normalize_theme_mode,
    _resolve_theme_is_dark,
    _system_prefers_dark,
)
from .final_behaviors import _v465_apply_theme

__all__ = [
    "_normalize_accent_color",
    "_normalize_theme_mode",
    "_resolve_theme_is_dark",
    "_system_prefers_dark",
    "_v465_apply_theme",
]


