"""Settings persistence, GUI theme normalization, and portable import/export."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ..app_info import _APP_VERSION
from ..logging_setup import logger
from .secrets import (
    _REDACTED_PLACEHOLDER,
    _SETTINGS_SCHEMA_VERSION,
    _is_secret_setting_key,
)

_SETTINGS_FILE = os.path.join(
    os.path.expanduser("~"), ".cyoa_downloader", "settings.json"
)
_SETTINGS_DEFAULTS: Dict[str, Any] = {
    "cyoa_mgr_enabled": None,
    "cyoa_mgr_db_path": "",
    "ai_api_key": "",
    "ai_enabled": False,
    "ai_provider": "anthropic",
    "ai_model": "claude-sonnet-4-6",
    "ai_mode": "auto_fallback",
    "ai_key_storage": "session",
    "ai_api_key_anthropic": "",
    "ai_api_key_openai": "",
    "ai_api_key_gemini": "",
    "ai_api_key_deepseek": "",
    "ai_api_key_qwen": "",
    "ai_api_key_groq": "",
    "ai_api_key_openrouter": "",
    "ai_api_key_custom": "",
    "ai_custom_base_url": "",
    "ai_temperature": None,
    "ollama_url": "http://localhost:11434",
    "ai_max_calls_per_download": 3,
    "ai_max_html_chars": 8000,
    "ai_max_js_chars": 14000,
    "ai_confirm_large_payload": True,
    "language": "en",
    "theme_mode": "System",
    "theme_accent_color": "#3b82f6",
    "http2_enabled": False,
    "dns": "",
    "bebasdns_variant": "",
    "gallery_dl_mode": "off",
    "auto_detect_output": "folder",
    "cloudflare_mode": "auto",
    "flaresolverr_url": "http://localhost:8191/v1",
    "flaresolverr_session_policy": "reuse-domain",
    "flaresolverr_timeout": 60,
    "flaresolverr_wait_after": 3,
    "flaresolverr_proxy_mode": "inherit",
    "deep_scan_enabled": True,
    "selenium_enabled": True,
    "serve_enabled": True,
    "cheat_enabled": True,
    "archive_strategy": "classic",
    "archive_max_pages": 300,
    "archive_max_depth": 30,
    "archive_capture_interactions": False,
    "archive_interaction_policy": "safe",
    "archive_runtime_max_pages": 12,
    "archive_settle_time_ms": 1800,
    "archive_max_scroll_steps": 100,
    "archive_max_interactions": 20,
    "archive_no_progress_rounds": 2,
    "itch_enabled": False,
    "itch_api_key": "",
    "itch_key_storage": "session",
}

# settings.json stays flat for compatibility with every historical caller, but
# is written in these human-friendly sections. JSON allows whitespace between
# properties, so the saved file gets a blank line between each group.
_SETTINGS_GROUPS = (
    ("Interface and output", (
        "language", "theme_mode", "theme_accent_color", "auto_detect_output",
    )),
    ("JavaScript website archive", (
        "archive_strategy", "archive_max_pages", "archive_max_depth",
        "archive_interaction_policy", "archive_runtime_max_pages",
        "archive_settle_time_ms", "archive_max_scroll_steps",
        "archive_max_interactions", "archive_no_progress_rounds",
        "archive_capture_interactions", "deep_scan_enabled", "selenium_enabled",
        "serve_enabled", "cheat_enabled",
    )),
    ("Network and Cloudflare", (
        "http2_enabled", "dns", "bebasdns_variant", "cloudflare_mode",
        "flaresolverr_url", "flaresolverr_session_policy",
        "flaresolverr_timeout", "flaresolverr_wait_after", "flaresolverr_proxy_mode",
    )),
    ("CYOA and gallery integrations", (
        "cyoa_mgr_enabled", "cyoa_mgr_db_path", "gallery_dl_mode",
    )),
    ("AI assist", (
        "ai_enabled", "ai_provider", "ai_model", "ai_mode", "ai_key_storage",
        "ai_temperature", "ollama_url", "ai_max_calls_per_download",
        "ai_max_html_chars", "ai_max_js_chars", "ai_confirm_large_payload",
        "ai_custom_base_url", "ai_api_key", "ai_api_key_anthropic",
        "ai_api_key_openai", "ai_api_key_gemini", "ai_api_key_deepseek",
        "ai_api_key_qwen", "ai_api_key_groq", "ai_api_key_openrouter",
        "ai_api_key_custom",
    )),
    ("itch.io", (
        "itch_enabled", "itch_key_storage", "itch_api_key",
    )),
)

_SETTINGS_ENUMS = {
    "language": {"id", "en"},
    "theme_mode": {"Dark", "Light", "System"},
    "auto_detect_output": {"folder", "zip"},
    "archive_strategy": {"classic", "smart", "browser", "auto"},
    "archive_interaction_policy": {"off", "safe"},
    "gallery_dl_mode": {"off", "smart", "force"},
    "cloudflare_mode": {"off", "auto", "cloudscraper", "flaresolverr"},
    "flaresolverr_session_policy": {"temporary", "reuse-domain", "manual"},
    "flaresolverr_proxy_mode": {"inherit", "none"},
    "ai_key_storage": {"session", "env", "keyring", "plain"},
    "itch_key_storage": {"session", "keyring", "plain"},
}

_SETTINGS_INT_RANGES = {
    "archive_max_pages": (1, 5000),
    "archive_max_depth": (0, 100),
    "archive_runtime_max_pages": (1, 100),
    "archive_settle_time_ms": (250, 15000),
    "archive_max_scroll_steps": (1, 1000),
    "archive_max_interactions": (0, 100),
    "archive_no_progress_rounds": (1, 10),
    "flaresolverr_timeout": (1, 600),
    "flaresolverr_wait_after": (0, 60),
    "ai_max_calls_per_download": (0, 100),
    "ai_max_html_chars": (1000, 2_000_000),
    "ai_max_js_chars": (1000, 2_000_000),
}


def _settings_metadata() -> Dict[str, Any]:
    return {
        "format": "CYOA Downloader settings (flat compatibility format)",
        "schema_version": _SETTINGS_SCHEMA_VERSION,
        "app_version": _APP_VERSION,
        "edit_note": "Edit values, not key names. Invalid values fall back to safe defaults.",
        "guide": "GUI: Help / Guide > JavaScript Archive; docs/JAVASCRIPT_ARCHIVE_GUIDE.md",
        "archive_modes": {
            "classic": "Original single-page behavior (default)",
            "smart": "Classic plus bounded same-story route crawling",
            "browser": "Smart plus assets observed while JavaScript runs",
            "auto": "Fingerprint the site and choose Classic, Smart, Browser, or a structured adapter",
        },
        "archive_limits": {
            "archive_max_pages": "1..5000",
            "archive_max_depth": "0..100",
            "recommended_large_story": {"archive_max_pages": 800, "archive_max_depth": 30},
        },
        "archive_capture_interactions": (
            "Legacy compatibility setting. Safe interaction behavior is controlled by "
            "archive_interaction_policy."
        ),
        "archive_interaction_policy": {
            "off": "Never click runtime controls",
            "safe": "Allowlisted non-form controls only; mutation requests and navigation are blocked",
        },
        "secret_note": "Prefer session/keyring storage. Plain API-key fields are intentionally visible here.",
    }


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return default


def _normalize_loaded_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize hand-edited settings without changing the public flat shape."""
    clean = {k: v for k, v in data.items() if k != "_meta"}
    merged = {**_SETTINGS_DEFAULTS, **clean}

    for key, default in _SETTINGS_DEFAULTS.items():
        value = merged.get(key)
        if isinstance(default, bool):
            merged[key] = _coerce_bool(value, default)
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                merged[key] = int(value)
            except (TypeError, ValueError, OverflowError):
                merged[key] = default
        elif isinstance(default, str) and not isinstance(value, str):
            merged[key] = default

    for key, allowed in _SETTINGS_ENUMS.items():
        value = merged.get(key)
        if value not in allowed:
            if isinstance(value, str):
                canonical = {item.casefold(): item for item in allowed}.get(value.strip().casefold())
                if canonical is not None:
                    merged[key] = canonical
                    continue
            merged[key] = _SETTINGS_DEFAULTS[key]
    for key, (minimum, maximum) in _SETTINGS_INT_RANGES.items():
        value = merged.get(key, _SETTINGS_DEFAULTS[key])
        merged[key] = max(minimum, min(maximum, int(value)))

    manager = merged.get("cyoa_mgr_enabled")
    if manager is not None and not isinstance(manager, bool):
        merged["cyoa_mgr_enabled"] = None
    temperature = merged.get("ai_temperature")
    if temperature is not None:
        try:
            merged["ai_temperature"] = max(0.0, min(2.0, float(temperature)))
        except (TypeError, ValueError, OverflowError):
            merged["ai_temperature"] = None
    return merged


def _ordered_settings_payload(settings: Dict[str, Any]) -> Dict[str, Any]:
    clean = {k: v for k, v in settings.items() if k != "_meta"}
    payload: Dict[str, Any] = {"_meta": _settings_metadata()}
    seen = set()
    for _section, keys in _SETTINGS_GROUPS:
        for key in keys:
            payload[key] = clean.get(key, _SETTINGS_DEFAULTS[key])
            seen.add(key)
    # Preserve extension/forward-compatible keys after all known categories.
    for key in sorted(k for k in clean if k not in seen):
        payload[key] = clean[key]
    return payload


def _format_settings_json(settings: Dict[str, Any]) -> str:
    text = json.dumps(_ordered_settings_payload(settings), indent=2, ensure_ascii=False)
    first_keys = [keys[0] for _section, keys in _SETTINGS_GROUPS if keys]
    for key in first_keys:
        text = text.replace(f'\n  "{key}":', f'\n\n  "{key}":', 1)
    return text + "\n"


def _load_settings() -> Dict[str, Any]:
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("top-level value must be a JSON object")
            # Accept a portable export pasted in as the active file too.
            source = data.get("settings") if isinstance(data.get("settings"), dict) else data
            merged = _normalize_loaded_settings(source)
            # Migration: old versions stored Anthropic keys directly in settings.json.
            # Preserve old behavior only when an old key exists and no explicit storage mode was saved.
            if source.get("ai_api_key") and "ai_key_storage" not in source:
                merged["ai_key_storage"] = "plain"
            return merged
    except Exception as e:
        logger.warning(f"settings.json unreadable ({e}) — using defaults; "
                       f"backup saved as settings.json.corrupt")
        try:
            shutil.copy2(_SETTINGS_FILE, _SETTINGS_FILE + ".corrupt")
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _load_settings: %s", _ignored_exc)
    return dict(_SETTINGS_DEFAULTS)


def _save_settings(settings: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_SETTINGS_FILE)) or ".", exist_ok=True)
        tmp = _SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_format_settings_json(_normalize_loaded_settings(settings)))
        os.replace(tmp, _SETTINGS_FILE)
    except Exception as e:
        logger.warning(f"Could not save settings: {e}")


_SETTINGS_LOCK = threading.Lock()


def _update_setting(key: str, value: Any) -> None:
    """Thread-safe single-key update that never drops a concurrent change."""
    with _SETTINGS_LOCK:
        s = _load_settings()
        s[key] = value
        _save_settings(s)


def _update_settings(updates: Dict[str, Any]) -> None:
    """Thread-safe multi-key update under one lock acquisition."""
    with _SETTINGS_LOCK:
        s = _load_settings()
        s.update(updates)
        _save_settings(s)


_THEME_MODE_CANONICAL = {
    "dark": "Dark",
    "light": "Light",
    "system": "System",
}


def _normalize_theme_mode(value: Any) -> str:
    """Return a CTk-compatible theme mode; unknown values fall back to System."""
    mode = _THEME_MODE_CANONICAL.get(str(value or "").strip().lower())
    return mode or "System"


def _normalize_accent_color(value: Any, fallback: str = "#3b82f6") -> str:
    """Accept only #RRGGBB accent colors; ignore unsafe/invalid values."""
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    return fallback


def _system_prefers_dark() -> bool:
    """Best-effort OS theme probe used only when GUI theme is set to System."""
    try:
        import platform as _platform
        system = _platform.system().lower()
        if system == "windows":
            try:
                import winreg  # type: ignore
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                )
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
            except Exception:
                return True
        if system == "darwin":
            try:
                import subprocess as _sp
                r = _sp.run(
                    ["defaults", "read", "-g", "AppleInterfaceStyle"],
                    stdout=_sp.PIPE, stderr=_sp.DEVNULL, timeout=2, text=True,
                )
                return "dark" in (r.stdout or "").lower()
            except Exception:
                return True
        env_theme = (
            os.environ.get("GTK_THEME")
            or os.environ.get("XDG_CURRENT_DESKTOP")
            or os.environ.get("COLORFGBG")
            or ""
        ).lower()
        if "light" in env_theme:
            return False
        if "dark" in env_theme:
            return True
    except Exception:
        pass
    return True


def _resolve_theme_is_dark(mode: Any) -> bool:
    normalized = _normalize_theme_mode(mode)
    if normalized == "Light":
        return False
    if normalized == "System":
        return _system_prefers_dark()
    return True


def _detect_ffmpeg_path() -> Optional[str]:
    """Return ffmpeg executable path if available on PATH; never raises."""
    try:
        return shutil.which("ffmpeg")
    except Exception:
        return None


def _ffmpeg_install_guide() -> str:
    return (
        "FFMPEG install guide:\n"
        "  Windows : winget install Gyan.FFmpeg  OR  choco install ffmpeg; then reopen Terminal.\n"
        "  Linux   : sudo apt install ffmpeg  OR  sudo dnf install ffmpeg  OR  sudo pacman -S ffmpeg.\n"
        "  macOS   : brew install ffmpeg.\n"
        "  Verify  : ffmpeg -version\n"
        "  Note    : Missing ffmpeg only disables/limits media conversion features that need it; normal JSON/asset downloads continue."
    )


def export_settings(path: str) -> Tuple[bool, str]:
    """Write a redacted settings.json copy. Secrets are dropped, not masked-in.

    Returns (ok, message). Never raises. Never writes a secret value. A
    placeholder is recorded under "_redacted" purely as an informational list
    of which keys were withheld; their values are not included.
    """
    try:
        with _SETTINGS_LOCK:
            current = _load_settings()
        safe: Dict[str, Any] = {}
        redacted = []
        for k, v in current.items():
            if _is_secret_setting_key(k):
                redacted.append(k)
                continue
            safe[k] = v
        payload = {
            "_meta": {
                "app_version": _APP_VERSION,
                "schema_version": _SETTINGS_SCHEMA_VERSION,
                "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "redacted_keys": sorted(redacted),
            },
            "settings": safe,
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True, (f"Settings exported to {path} "
                      f"({len(safe)} keys, {len(redacted)} secret keys withheld).")
    except Exception as e:
        return False, f"Settings export failed: {e}"


def import_settings(path: str) -> Tuple[bool, str]:
    """Merge a settings export back in, validating and ignoring secrets.

    Import MERGES (existing unspecified keys are preserved). Never raises.
    """
    try:
        if not os.path.exists(path):
            return False, f"Import failed: file not found: {path}"
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"Import failed: invalid JSON ({e})."
    except Exception as e:
        return False, f"Import failed: {e}"

    if isinstance(raw, dict) and isinstance(raw.get("settings"), dict):
        incoming = raw["settings"]
    elif isinstance(raw, dict):
        incoming = raw
    else:
        return False, "Import failed: unexpected JSON structure (expected an object)."

    accepted: Dict[str, Any] = {}
    skipped_secret = 0
    skipped_unknown = 0
    skipped_type = 0
    skipped_redacted = 0
    extra_in = incoming.get("extra") if isinstance(incoming.get("extra"), dict) else None

    for k, v in incoming.items():
        if k == "extra":
            continue
        if v == _REDACTED_PLACEHOLDER:
            skipped_redacted += 1
            continue
        if _is_secret_setting_key(k):
            skipped_secret += 1
            continue
        if k not in _SETTINGS_DEFAULTS:
            skipped_unknown += 1
            continue
        default_v = _SETTINGS_DEFAULTS[k]
        if isinstance(default_v, bool):
            if not isinstance(v, bool):
                skipped_type += 1
                continue
        elif isinstance(default_v, int) and not isinstance(default_v, bool):
            if not isinstance(v, int) or isinstance(v, bool):
                skipped_type += 1
                continue
        elif isinstance(default_v, str):
            if not isinstance(v, str):
                skipped_type += 1
                continue
        accepted[k] = v

    if extra_in:
        clean_extra = {ek: ev for ek, ev in extra_in.items()
                       if not _is_secret_setting_key(ek) and ev != _REDACTED_PLACEHOLDER}
        if clean_extra:
            accepted["extra"] = clean_extra

    if not accepted:
        return False, ("Import found no safe, known settings to apply "
                       f"(secret={skipped_secret}, unknown={skipped_unknown}, "
                       f"type-mismatch={skipped_type}, redacted={skipped_redacted}).")

    _update_settings(accepted)
    return True, (f"Imported {len(accepted)} settings (merged). Skipped: "
                  f"{skipped_secret} secret, {skipped_unknown} unknown, "
                  f"{skipped_type} type-mismatch, {skipped_redacted} redacted.")
