"""Internal helpers for Phase 3 network compatibility bridges."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def legacy() -> ModuleType:
    mod = sys.modules.get("cyoa_downloader_app.runtime.surface")
    if mod is not None:
        return mod
    return importlib.import_module("cyoa_downloader_app.runtime.surface")
