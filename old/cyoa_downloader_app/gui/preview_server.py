"""Local preview/serve helper domain module.

Phase 24 moves the preview-token lifecycle out of ``legacy.py``. The serve
feature toggle remains a compatibility bridge because ``run_download`` still
reads the historical ``_SERVE_ENABLED`` global until the orchestrator is fully
moved.
"""

from __future__ import annotations

from ..core.feature_flags import _set_serve_enabled
from ..core.preview_token import (
    _PREVIEW_SESSION_TOKEN,
    _PREVIEW_TOKEN_LOCK,
    _clear_preview_token,
    _current_preview_token,
    _new_preview_token,
    _preview_token_valid,
)
from ..preview_assets import (
    _BUNDLED_INTCYOAENHANCER_USERSCRIPT,
    _INT_CYOA_ENHANCER_INFO,
    userscript_integration_report,
)

__all__ = [
    "_BUNDLED_INTCYOAENHANCER_USERSCRIPT",
    "_INT_CYOA_ENHANCER_INFO",
    "_PREVIEW_SESSION_TOKEN",
    "_PREVIEW_TOKEN_LOCK",
    "_clear_preview_token",
    "_current_preview_token",
    "_new_preview_token",
    "_preview_token_valid",
    "_set_serve_enabled",
    "userscript_integration_report",
]
