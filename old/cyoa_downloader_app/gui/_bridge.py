"""Internal helpers for GUI compatibility bridges.

The Phase 7 GUI extraction intentionally keeps the historical implementation in
``legacy.py`` while giving the GUI base class a stable domain-module home. Later
phases can move methods out of legacy without changing import paths again.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def legacy() -> ModuleType:
    """Return the compatibility surface module without importing it twice."""
    mod = sys.modules.get("cyoa_downloader_app.runtime.surface")
    if mod is not None:
        return mod
    return importlib.import_module("cyoa_downloader_app.runtime.surface")
