"""Late bridge to the legacy implementation during integration extraction."""

from __future__ import annotations


def legacy():
    """Return the transitional legacy module without importing it at package import time."""
    from ..runtime import surface as _surface
    return _surface
