"""Compatibility shim for the historical ``gui.patches`` import path."""

from __future__ import annotations

from .composition import (  # noqa: F401
    PATCH_ORDER,
    _verify_patch_surface,
    applied_patch_order,
    apply_gui_patches,
    apply_v24,
    apply_v25,
    apply_v27,
    apply_v46,
    apply_v462,
    apply_v463,
    apply_v465,
    apply_v466,
    expected_patch_surface,
)

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
