"""Logging setup and secret redaction helpers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("cyoa_downloader")

_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

_SECRET_LOG_RE = re.compile(
    r'(?i)\b(api[_-]?key|token|password|passwd|secret|cookie|authorization|credential|bearer)\b'
    r'(\s*[:=]\s*|[\'"]?\s*:\s*[\'"]?)'
    r'([^,\s\'"}]{6,}|[\'"][^\'"]{6,}[\'"])'
)

_BEARER_LOG_RE = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}')

def _redact_sensitive_text(value: Any) -> str:
    """Return log-safe text with token/password/cookie-like values withheld."""
    try:
        text = str(value)
    except Exception:
        return "<unprintable>"
    text = _BEARER_LOG_RE.sub("Bearer __REDACTED__", text)
    return _SECRET_LOG_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}__REDACTED__", text)

class _SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact_sensitive_text(record.getMessage())
            record.args = ()
        except Exception as _ignored_exc:
            record.msg = "<unprintable log record>"; record.args = ()
        return True

logger.setLevel(logging.INFO)
# Prevent duplicate console lines when the module is reloaded or embedded.
logger.propagate = False
if not any(isinstance(f, _SecretRedactionFilter) for f in logger.filters):
    logger.addFilter(_SecretRedactionFilter())
_stream_handler = next(
    (h for h in logger.handlers if getattr(h, "_cyoa_console_handler", False)),
    None,
)
if _stream_handler is None:
    _stream_handler = logging.StreamHandler()
    setattr(_stream_handler, "_cyoa_console_handler", True)
    _stream_handler.setFormatter(_formatter)
    logger.addHandler(_stream_handler)
else:
    _stream_handler.setFormatter(_formatter)

# File logging is initialized lazily when a download starts so output_dir is known.
_file_handler: Optional[logging.Handler] = None

def setup_file_logging(output_dir: str) -> None:
    """Attach a rotating file handler. Guards against duplicate calls."""
    global _file_handler
    from logging.handlers import RotatingFileHandler
    # Remove ALL existing file/rotating handlers (prevents duplicate log lines)
    for h in logger.handlers[:]:
        if isinstance(h, RotatingFileHandler) or isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in setup_file_logging (line 375): %s", _ignored_exc)
    _file_handler = None
    log_path = os.path.join(output_dir, "cyoa_downloader.log")
    try:
        os.makedirs(output_dir, exist_ok=True)
        fh = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        fh.setFormatter(_formatter)
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
        _file_handler = fh
        logger.info(f"Log file: {log_path}")
    except Exception as e:
        logger.warning(f"Could not create log file at {log_path}: {e}")

__all__ = [
    "logger", "_formatter", "_redact_sensitive_text", "_SecretRedactionFilter",
    "setup_file_logging",
]
