"""Central binding point for GUI panel method groups.

Panel-domain modules expose the composed GUI method objects; attaching through
this module is deliberately mechanical and idempotent.
"""

from __future__ import annotations

from typing import Dict, Tuple, Type, TypeVar

T = TypeVar("T")

# Pre-bind empty values before importing the bridge-backed panel modules. If
# this package is imported directly, a nested runtime bootstrap can safely call
# attach_panel_methods while module initialization is in progress; the final
# mappings are attached once the imports below complete.
PANEL_MODULES: Tuple[object, ...] = ()
PANEL_BIND_ORDER: Tuple[str, ...] = ()


def panel_method_names() -> Tuple[str, ...]:
    names = []
    for module in PANEL_MODULES:
        names.extend(module.PANEL_METHOD_NAMES)
    return tuple(names)


def panel_method_map() -> Dict[str, object]:
    methods: Dict[str, object] = {}
    for module in PANEL_MODULES:
        methods.update(module.PANEL_METHODS)
    return methods


def attach_panel_methods(cls: Type[T]) -> Type[T]:
    """Attach all panel-group methods to *cls* and record bind metadata."""
    for name, fn in panel_method_map().items():
        setattr(cls, name, fn)
    setattr(cls, "_cyoa_gui_panel_bind_order", PANEL_BIND_ORDER)
    setattr(cls, "_cyoa_gui_panel_method_names", panel_method_names())
    return cls


def bound_panel_methods(cls: Type[object]) -> Tuple[str, ...]:
    """Return panel method names recorded on a GUI class."""
    return tuple(getattr(cls, "_cyoa_gui_panel_method_names", ()))


from . import ai, batch, cache, cloudflare, credits, cyoa_manager, diagnostics, guide, offline_viewers, settings, updates

PANEL_MODULES = (
    settings,
    cloudflare,
    batch,
    diagnostics,
    cache,
    cyoa_manager,
    ai,
    updates,
    credits,
    guide,
    offline_viewers,
)
PANEL_BIND_ORDER = tuple(module.__name__.rsplit(".", 1)[-1] for module in PANEL_MODULES)

# Repair the intentionally empty early binding used only by a direct-import
# cycle. In the normal facade path bootstrap performs the same idempotent bind.
try:
    from ..app import CYOADownloaderGUI as _CYOADownloaderGUI
    attach_panel_methods(_CYOADownloaderGUI)
except Exception:
    pass


__all__ = [
    "PANEL_MODULES",
    "PANEL_BIND_ORDER",
    "attach_panel_methods",
    "bound_panel_methods",
    "panel_method_names",
    "panel_method_map",
]
