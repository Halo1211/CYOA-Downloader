"""Compatibility bridge for GUI panel extraction.

Panel modules import methods from the already-composed GUI class. This keeps
object identity intact while giving each panel group a stable module home for
later physical extraction.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Type, TypeVar

from .._bridge import legacy

T = TypeVar("T")

_l = legacy()
_COMPOSED_GUI_CLASS = _l.CYOADownloaderGUI


def legacy_method(name: str) -> Callable[..., object]:
    """Return the final composed GUI method named *name*."""
    try:
        return getattr(_COMPOSED_GUI_CLASS, name)
    except AttributeError as exc:  # fail early if a panel name drifts
        raise RuntimeError(f"GUI method missing during panel bridge: {name}") from exc


def method_map(names: Iterable[str]) -> Dict[str, Callable[..., object]]:
    """Build a deterministic method mapping from the composed GUI class."""
    return {name: legacy_method(name) for name in names}


def attach_methods(cls: Type[T], methods: Dict[str, Callable[..., object]]) -> Type[T]:
    """Attach *methods* to *cls* without wrapping or changing call signatures."""
    for name, fn in methods.items():
        setattr(cls, name, fn)
    return cls
