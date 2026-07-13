"""Internal helpers for Phase 6 download-pipeline compatibility bridges."""

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
