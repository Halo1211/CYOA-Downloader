"""Logging setup and secret redaction helpers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("cyoa_downloader")

# Logging is itself a last-resort boundary: hostile ``__str__`` methods and
# malformed LogRecord objects must never break the caller being diagnosed.
_LOGGING_BOUNDARY_ERRORS = (Exception,)

_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

_SECRET_LOG_RE = re.compile(
    r'(?i)\b('
    r'(?:[a-z0-9]+[_-])*api[_-]?key(?:[_-][a-z0-9]+)*|'
    r'(?:[a-z0-9]+[_-])*(?:token|password|passwd|secret|cookie|authorization|credential|bearer)'
    r'(?:[_-][a-z0-9]+)*'
    r')\b'
    r'(\s*[:=]\s*|[\'"]?\s*:\s*[\'"]?)'
    r'([^,\s\'"}]{6,}|[\'"][^\'"]{6,}[\'"])'
)

_BEARER_LOG_RE = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}')
_URL_USERINFO_RE = re.compile(
    r"(?i)\b(?P<scheme>https?|socks[45]h?)://"
    r"(?P<username>[^/@:\s]+):(?P<password>[^/@\s]+)@"
)

def _redact_sensitive_text(value: Any) -> str:
    """Return log-safe text with token/password/cookie-like values withheld."""
    try:
        text = str(value)
    except _LOGGING_BOUNDARY_ERRORS:
        return "<unprintable>"
    text = _BEARER_LOG_RE.sub("Bearer __REDACTED__", text)
    text = _URL_USERINFO_RE.sub(
        lambda match: (
            f"{match.group('scheme')}://{match.group('username')}:__REDACTED__@"
        ),
        text,
    )
    return _SECRET_LOG_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}__REDACTED__", text)

class _SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact_sensitive_text(record.getMessage())
            record.args = ()
        except _LOGGING_BOUNDARY_ERRORS:
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
    _stream_handler._cyoa_console_handler = True
    _stream_handler.setFormatter(_formatter)
    logger.addHandler(_stream_handler)
else:
    _stream_handler.setFormatter(_formatter)

# File logging is initialized lazily when a download starts so output_dir is known.
_file_handler: logging.Handler | None = None

def setup_file_logging(output_dir: str) -> None:
    """Attach a rotating file handler. Guards against duplicate calls."""
    global _file_handler
    from logging.handlers import RotatingFileHandler
    # Remove ALL existing file/rotating handlers (prevents duplicate log lines)
    for h in logger.handlers[:]:
        if isinstance(h, (RotatingFileHandler, logging.FileHandler)):
            logger.removeHandler(h)
            try:
                h.close()
            except (OSError, RuntimeError, ValueError) as _ignored_exc:
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
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning(f"Could not create log file at {log_path}: {e}")

__all__ = [
    "_SecretRedactionFilter",
    "_formatter",
    "_redact_sensitive_text",
    "logger",
    "setup_file_logging",
]
