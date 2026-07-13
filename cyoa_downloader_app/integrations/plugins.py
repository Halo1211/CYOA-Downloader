"""Internal plugin registry.

Phase 21 moves the small registry implementation out of legacy.py while keeping
object identity stable through legacy imports. Built-in registration remains
explicit and idempotent.
"""

from __future__ import annotations

import threading
import inspect
from typing import Any, Dict, List, Optional, Set

from ..logging_setup import logger


class _PluginRegistry:
    """Name → callable registry with deterministic override + isolation."""

    def __init__(self, kind: str):
        self._kind = kind
        self._plugins: Dict[str, Any] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    def register(self, name: str, fn, override: bool = False) -> None:
        if not name or not callable(fn):
            raise ValueError(f"{self._kind} plugin needs a name and a callable")
        with self._lock:
            if name in self._plugins and not override:
                raise ValueError(
                    f"{self._kind} plugin '{name}' already registered "
                    f"(pass override=True to replace deterministically)")
            if name not in self._plugins:
                self._order.append(name)
            self._plugins[name] = fn

    def unregister(self, name: str) -> bool:
        with self._lock:
            if name in self._plugins:
                del self._plugins[name]
                self._order = [n for n in self._order if n != name]
                return True
            return False

    def names(self) -> List[str]:
        with self._lock:
            return list(self._order)

    def items(self):
        with self._lock:
            return [(n, self._plugins[n]) for n in self._order]


_ASSET_SCANNER_PLUGINS = _PluginRegistry("asset-scanner")
_ENGINE_DETECTOR_PLUGINS = _PluginRegistry("engine-detector")


def register_asset_scanner(name: str, fn, override: bool = False) -> None:
    """Public extension point: add an asset-scanner plugin."""
    _ASSET_SCANNER_PLUGINS.register(name, fn, override=override)


def register_engine_detector(name: str, fn, override: bool = False) -> None:
    """Public extension point: add an engine-detector plugin."""
    _ENGINE_DETECTOR_PLUGINS.register(name, fn, override=override)


def run_asset_scanner_plugins(
    text: str,
    file_url: str,
    base_url: str,
    file_ext: str = ".js",
) -> Set[str]:
    """Run every registered asset scanner, union their results, isolate failures."""
    out: Set[str] = set()
    for name, fn in _ASSET_SCANNER_PLUGINS.items():
        try:
            res = fn(text, file_url, base_url, file_ext)
            if res:
                out |= set(res)
        except Exception as e:
            logger.debug(f"[plugin:scanner:{name}] failed on {file_url}: {e}")
    return out


def run_engine_detector_plugins(html_text: str, mode: str = "auto") -> Optional[Dict]:
    """First detector that returns a non-None dict wins; failures are isolated."""
    for name, fn in _ENGINE_DETECTOR_PLUGINS.items():
        try:
            res = fn(html_text, mode)
            if res:
                return res
        except Exception as e:
            logger.debug(f"[plugin:detector:{name}] failed: {e}")
    return None


def _register_builtin_plugins(scanner=None, detector=None) -> None:
    """Register built-in scanner/detector idempotently.

    Optional arguments keep this module independent from legacy.py. When omitted,
    the current transitional implementations are imported lazily.
    """
    if scanner is None:
        from ..download.asset_scan import _scan_file_for_assets as scanner
    if detector is None:
        from .offline_viewers.registry import get_viewer_for_site as detector
    try:
        register_asset_scanner("builtin", scanner)
    except ValueError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _register_builtin_plugins: %s", _ignored_exc)
    try:
        register_engine_detector("builtin", detector)
    except ValueError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _register_builtin_plugins: %s", _ignored_exc)


# Historical public signature was no-argument; optional parameters are internal test hooks.
_register_builtin_plugins.__signature__ = inspect.Signature()

__all__ = [
    "_PluginRegistry",
    "_ASSET_SCANNER_PLUGINS",
    "_ENGINE_DETECTOR_PLUGINS",
    "register_asset_scanner",
    "register_engine_detector",
    "run_asset_scanner_plugins",
    "run_engine_detector_plugins",
    "_register_builtin_plugins",
]
