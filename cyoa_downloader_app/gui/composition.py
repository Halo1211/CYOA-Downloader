"""Final GUI composition verifier.

``gui.bootstrap`` owns the concrete historical GUI behavior bindings through one
explicit binding gate. This module keeps the public order/verification API for
that already-composed GUI class without depending on versioned behavior modules.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple, Type, TypeVar

T = TypeVar("T")

PATCH_ORDER: Tuple[str, ...] = (
    "v24",
    "v25",
    "v27",
    "v46",
    "v462",
    "v463",
    "v465",
    "v466",
)

_PATCH_SENTINEL_ATTR = "_cyoa_gui_patch_pipeline_order"
_PATCH_MODE_ATTR = "_cyoa_gui_patch_pipeline_mode"

# Minimal method/attribute surface expected after GUI bootstrap has composed the
# class. These names are stable anchors instead of full behavior assertions.
_EXPECTED_PATCH_SURFACE: Dict[str, Tuple[str, ...]] = {
    "v25": (
        "_ai_settings_panel",
        "_manage_offline_viewers",
        "_cloudflare_panel",
    ),
    "v27": (
        "_cache_manager_panel",
        "_check_updates_panel",
    ),
    "v46": (
        "_v46_poll_progress",
        "_v46_enqueue_progress",
        "_v46_on_close",
        "_v46_toggle_progress_panel",
    ),
    "v465": (
        "_safe_message",
        "_apply_theme",
    ),
}


def _mark(cls: Type[T], step_id: str) -> Type[T]:
    """Record that *step_id* passed through the central composition gate."""
    current = list(getattr(cls, _PATCH_SENTINEL_ATTR, ()))
    if step_id not in current:
        current.append(step_id)
    setattr(cls, _PATCH_SENTINEL_ATTR, tuple(current))
    return cls


def _verify_patch_surface(cls: Type[T], *, strict: bool = True) -> List[str]:
    """Return missing final GUI anchors; raise in strict mode."""
    missing: List[str] = []
    for step_id, names in _EXPECTED_PATCH_SURFACE.items():
        for name in names:
            if not hasattr(cls, name):
                missing.append(f"{step_id}:{name}")
    if missing and strict:
        joined = ", ".join(missing)
        raise RuntimeError(f"GUI composition surface incomplete after bootstrap: {joined}")
    return missing


def _composed_bridge(cls: Type[T], step_id: str) -> Type[T]:
    """Compatibility placeholder for a historical composition step."""
    return _mark(cls, step_id)


def apply_v24(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v24")


def apply_v25(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v25")


def apply_v27(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v27")


def apply_v46(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v46")


def apply_v462(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v462")


def apply_v463(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v463")


def apply_v465(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v465")


def apply_v466(cls: Type[T]) -> Type[T]:
    return _composed_bridge(cls, "v466")


_COMPOSITION_STEPS: Tuple[Callable[[Type[T]], Type[T]], ...] = (
    apply_v24,
    apply_v25,
    apply_v27,
    apply_v46,
    apply_v462,
    apply_v463,
    apply_v465,
    apply_v466,
)


def apply_gui_patches(cls: Type[T]) -> Type[T]:
    """Verify the ordered GUI composition pipeline and return *cls*.

    The function name is preserved for compatibility with older imports.
    """
    for step in _COMPOSITION_STEPS:
        cls = step(cls)
    setattr(cls, _PATCH_SENTINEL_ATTR, PATCH_ORDER)
    setattr(cls, _PATCH_MODE_ATTR, "composed-bootstrap")
    _verify_patch_surface(cls, strict=True)
    return cls


def applied_patch_order(cls: Type[object]) -> Tuple[str, ...]:
    """Return the central compatibility order recorded on *cls*."""
    return tuple(getattr(cls, _PATCH_SENTINEL_ATTR, ()))


def expected_patch_surface() -> Dict[str, Tuple[str, ...]]:
    """Return a copy of the final GUI anchor map for tests/diagnostics."""
    return dict(_EXPECTED_PATCH_SURFACE)


__all__ = [
    "PATCH_ORDER",
    "apply_gui_patches",
    "applied_patch_order",
    "expected_patch_surface",
    "apply_v24",
    "apply_v25",
    "apply_v27",
    "apply_v46",
    "apply_v462",
    "apply_v463",
    "apply_v465",
    "apply_v466",
    "_verify_patch_surface",
]
