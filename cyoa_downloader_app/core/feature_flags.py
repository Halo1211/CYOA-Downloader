"""Runtime feature toggles.

Phase 63 makes runtime.state the primary owner for these feature flags.  The
setters still mirror values to the loaded legacy facade for compatibility, but
they no longer import legacy.py just to update state.
"""

from __future__ import annotations

from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy


def _set_deep_scan_enabled(enabled: bool) -> None:
    """Enable/disable the JS/CSS deep-scan asset pass. Default ON."""
    state._DEEP_SCAN_ENABLED = bool(enabled)
    mirror_to_legacy("_DEEP_SCAN_ENABLED", state._DEEP_SCAN_ENABLED)
    logger.info(f"Deep scan {'enabled' if state._DEEP_SCAN_ENABLED else 'disabled'}." )


def _set_selenium_enabled(enabled: bool) -> None:
    """Enable/disable the headless browser image fallback. Default ON."""
    state._SELENIUM_ENABLED = bool(enabled)
    mirror_to_legacy("_SELENIUM_ENABLED", state._SELENIUM_ENABLED)
    logger.info(f"Selenium/headless fallback {'enabled' if state._SELENIUM_ENABLED else 'disabled'}." )


def _set_serve_enabled(enabled: bool) -> None:
    """Enable/disable the post-download local preview server. Default ON."""
    state._SERVE_ENABLED = bool(enabled)
    mirror_to_legacy("_SERVE_ENABLED", state._SERVE_ENABLED)
    logger.info(f"Serve preview {'enabled' if state._SERVE_ENABLED else 'disabled'}." )


def _set_cheat_enabled(enabled: bool) -> None:
    """Enable/disable bundled ICE/cheat injection during serve. Default ON."""
    state._CHEAT_ENABLED = bool(enabled)
    mirror_to_legacy("_CHEAT_ENABLED", state._CHEAT_ENABLED)
    logger.info(f"Cheat panel {'enabled' if state._CHEAT_ENABLED else 'disabled'}." )


__all__ = [
    "_set_deep_scan_enabled", "_set_selenium_enabled",
    "_set_serve_enabled", "_set_cheat_enabled",
]
