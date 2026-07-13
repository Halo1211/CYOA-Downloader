"""Runtime compatibility helpers used while legacy.py becomes import-only.

These helpers let domain modules read/write the new ``runtime.state`` owner
without importing ``cyoa_downloader_app.runtime.surface`` as their primary state store.
When the legacy module is already loaded, writes are mirrored back so old
private imports and tests still see the historical global names.
"""
from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from . import state


def legacy_module() -> ModuleType | None:
    """Return the loaded legacy module, if it exists, without importing it."""
    return sys.modules.get("cyoa_downloader_app.runtime.surface") or sys.modules.get("cyoa_downloader")


def mirror_to_legacy(name: str, value: Any) -> Any:
    """Mirror one runtime value to the loaded legacy facade, if any."""
    mod = legacy_module()
    if mod is not None:
        try:
            setattr(mod, name, value)
        except Exception:
            pass
    return value


def set_state_attr(name: str, value: Any, *, mirror: bool = True) -> Any:
    """Set ``runtime.state.<name>`` and optionally mirror it to legacy."""
    setattr(state, name, value)
    if mirror:
        mirror_to_legacy(name, value)
    return value


def get_state_attr(name: str, default: Any = None) -> Any:
    """Read from ``runtime.state`` first, then legacy as a compatibility fallback."""
    if hasattr(state, name):
        return getattr(state, name)
    mod = legacy_module()
    if mod is not None:
        return getattr(mod, name, default)
    return default


__all__ = ["legacy_module", "mirror_to_legacy", "set_state_attr", "get_state_attr"]
