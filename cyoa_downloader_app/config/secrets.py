"""Secret classification and lightweight credential helpers for settings-related code."""

from __future__ import annotations

# Schema version is bumped only when the settings shape changes in a way that
# importers must know about. Export/import are additive and never touch the
# download pipeline or output formats.
_SETTINGS_SCHEMA_VERSION = 1

# Keys that must NEVER leave the machine in an export. Anything matching an
# exact name OR any of the substring fragments below is dropped (denylist, not
# allowlist, so future secret-ish keys are caught by fragment too).
_SETTINGS_SECRET_KEYS = {
    "ai_api_key",
    "ai_api_key_anthropic",
    "ai_api_key_openai",
    "ai_api_key_gemini",
    "ai_api_key_deepseek",
    "ai_api_key_qwen",
    "ai_api_key_groq",
    "ai_api_key_openrouter",
    "ai_api_key_custom",
    "itch_api_key",
}
_SETTINGS_SECRET_FRAGMENTS = (
    "api_key", "apikey", "token", "password", "passwd", "secret",
    "cookie", "auth", "credential", "bearer",
)
_REDACTED_PLACEHOLDER = "__REDACTED__"


def _is_secret_setting_key(key: str) -> bool:
    k = str(key).lower()
    if key in _SETTINGS_SECRET_KEYS:
        return True
    return any(frag in k for frag in _SETTINGS_SECRET_FRAGMENTS)


def _keyring_module():
    """Return the optional keyring module, or None when unavailable."""
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


def _mask_secret(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "…" + value[-4:]
