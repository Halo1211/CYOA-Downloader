"""Local preview/serve helper domain module.

Phase 24 moves the preview-token lifecycle out of ``legacy.py``. The serve
feature toggle remains a compatibility bridge because ``run_download`` still
reads the historical ``_SERVE_ENABLED`` global until the orchestrator is fully
moved.
"""

from __future__ import annotations

from ..core.preview_token import (
    _PREVIEW_TOKEN_LOCK,
    _PREVIEW_SESSION_TOKEN,
    _new_preview_token,
    _current_preview_token,
    _clear_preview_token,
    _preview_token_valid,
)
from ..preview_assets import (
    _INT_CYOA_ENHANCER_INFO,
    _BUNDLED_INTCYOAENHANCER_USERSCRIPT,
    userscript_integration_report,
)


from ..core.feature_flags import _set_serve_enabled


__all__ = [
    "_INT_CYOA_ENHANCER_INFO",
    "_BUNDLED_INTCYOAENHANCER_USERSCRIPT",
    "userscript_integration_report",
    "_set_serve_enabled",
    "_PREVIEW_TOKEN_LOCK",
    "_PREVIEW_SESSION_TOKEN",
    "_new_preview_token",
    "_current_preview_token",
    "_clear_preview_token",
    "_preview_token_valid",
]
