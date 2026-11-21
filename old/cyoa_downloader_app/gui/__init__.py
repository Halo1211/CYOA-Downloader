"""GUI package exports.

The package uses lazy attribute loading so legacy.py can import small GUI helper
modules during its own initialization without forcing gui.app/panels to import
CYOADownloaderGUI before the legacy class exists.
"""

from __future__ import annotations

from typing import Any

__all__ = ["CYOADownloaderGUI", "launch_gui"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .app import CYOADownloaderGUI, launch_gui
        return {"CYOADownloaderGUI": CYOADownloaderGUI, "launch_gui": launch_gui}[name]
    raise AttributeError(name)
