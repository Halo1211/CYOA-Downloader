"""AI provider/key-storage core helpers extracted from legacy.py.

This module intentionally contains only the low-risk provider normalization,
key resolution, budget, and SSRF guard helpers. Network AI calls and GUI panel
bodies live in sibling integration/GUI modules.
"""

from __future__ import annotations

import os
import re
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..logging_setup import logger
from ..config.settings import _load_settings, _save_settings, _SETTINGS_LOCK
from ..config.secrets import _keyring_module, _mask_secret

# ── AI Assist provider + key storage ───────────────────────────────────────
AI_KEYRING_SERVICE = "cyoa_downloader"
_VALID_AI_KEY_STORAGE = {"session", "env", "keyring", "plain"}
_VALID_AI_MODES = {"off", "diagnostics", "auto_fallback", "aggressive_recovery"}
_VALID_AI_PROVIDERS = {"anthropic", "openai", "gemini", "ollama", "deepseek", "qwen", "groq", "openrouter", "custom"}
AI_PROVIDER_LABELS: Dict[str, str] = {
    "anthropic": "Anthropic Claude",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "ollama": "Ollama / Local",
    "deepseek": "DeepSeek",
    "qwen": "Qwen / DashScope",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "custom": "Custom (OpenAI-compatible)",
}
AI_PROVIDER_ENV_VARS: Dict[str, List[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "ollama": [],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "qwen": ["DASHSCOPE_API_KEY", "QWEN_API_KEY", "ALIYUN_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "custom": ["CUSTOM_AI_API_KEY", "OPENAI_API_KEY"],
}
# Base URLs for OpenAI-compatible chat-completions providers. "custom" reads
# its base URL from settings ("ai_custom_base_url") at call time.
AI_OPENAI_COMPAT_BASE: Dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
AI_MODEL_OPTIONS: Dict[str, List[str]] = {
    # Editable recommendations. Providers add/deprecate models over time; users can pass
    # any custom model id via CLI --ai-model or the GUI field. Treat these as
    # convenience presets, not a guarantee that a provider account has access.
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-5.5", "gpt-5.4", "gpt-4.1-mini"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.1-pro-preview", "gemini-3.5-flash"],
    "ollama": ["llama3.1", "qwen2.5-coder", "mistral", "gemma2"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "qwen": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen2.5-coder-32b-instruct"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
    "openrouter": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4-6", "google/gemini-2.5-flash"],
    "custom": ["gpt-4o-mini"],
}
AI_PROVIDER_DEFAULT_MODEL: Dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.5",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1",
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openai/gpt-4.1-mini",
    "custom": "gpt-4o-mini",
}
OLLAMA_DEFAULT_URL = "http://localhost:11434"


def _normalize_ai_provider(value: str) -> str:
    v = (value or "anthropic").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "claude": "anthropic",
        "anthropic_claude": "anthropic",
        "open_ai": "openai",
        "gpt": "openai",
        "google": "gemini",
        "google_gemini": "gemini",
        "local": "ollama",
        "ollama_local": "ollama",
        "deepseek_ai": "deepseek",
        "deep_seek": "deepseek",
        "dashscope": "qwen",
        "qwen_dashscope": "qwen",
        "aliyun": "qwen",
        "alibaba": "qwen",
    }
    v = aliases.get(v, v)
    return v if v in _VALID_AI_PROVIDERS else "anthropic"


def _ai_provider_label(provider: str) -> str:
    return AI_PROVIDER_LABELS.get(_normalize_ai_provider(provider), provider or "AI")


def _ai_env_vars(provider: Optional[str] = None) -> List[str]:
    return AI_PROVIDER_ENV_VARS.get(_normalize_ai_provider(provider or _get_ai_provider()), [])


def _ai_primary_env_var(provider: Optional[str] = None) -> str:
    vars_ = _ai_env_vars(provider)
    return vars_[0] if vars_ else ""


def _ai_model_options(provider: Optional[str] = None) -> List[str]:
    p = _normalize_ai_provider(provider or _get_ai_provider())
    return list(AI_MODEL_OPTIONS.get(p, []))


def _default_ai_model(provider: Optional[str] = None) -> str:
    p = _normalize_ai_provider(provider or _get_ai_provider())
    return AI_PROVIDER_DEFAULT_MODEL.get(p, "claude-sonnet-4-6")


def _normalize_ai_key_storage(value: str) -> str:
    v = (value or "session").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "environment": "env",
        "environment_variable": "env",
        "os_credential_manager": "keyring",
        "credential_manager": "keyring",
        "os_keyring": "keyring",
        "settings": "plain",
        "settings_json": "plain",
        "plain_settings_json": "plain",
        "session_only": "session",
    }
    v = aliases.get(v, v)
    return v if v in _VALID_AI_KEY_STORAGE else "session"


def _normalize_ai_mode(value: str) -> str:
    v = (value or "auto_fallback").strip().lower().replace("-", "_")
    aliases = {"auto": "auto_fallback", "aggressive": "aggressive_recovery", "diagnostics_only": "diagnostics"}
    v = aliases.get(v, v)
    return v if v in _VALID_AI_MODES else "auto_fallback"



def _ai_provider_needs_key(provider: Optional[str] = None) -> bool:
    """Return True if this provider needs a remote API key."""
    return _normalize_ai_provider(provider or _get_ai_provider()) != "ollama"


def _ai_is_available(api_key: str = "", provider: Optional[str] = None) -> bool:
    """Provider-aware availability check. Ollama/local does not require an API key."""
    p = _normalize_ai_provider(provider or _get_ai_provider())
    return (p == "ollama") or bool((api_key or "").strip())


def _ai_mode_allows(kind: str, mode: Optional[str] = None) -> bool:
    """Map AI Assist mode to concrete behavior.

    kind values:
      - diagnostics: non-mutating viewer diagnostics/logging
      - project_detect: AI can suggest a project.json URL and the app may fetch it
      - asset_scan: AI can suggest extra JS/CSS/image/audio candidates
    """
    m = _normalize_ai_mode(mode or _load_settings().get("ai_mode", "auto_fallback"))
    if m == "off":
        return False
    if m == "diagnostics":
        return kind == "diagnostics"
    if m == "auto_fallback":
        return kind in {"diagnostics", "project_detect"}
    if m == "aggressive_recovery":
        return kind in {"diagnostics", "project_detect", "asset_scan"}
    return False


def _get_ai_int_setting(name: str, default: int, *, min_value: int = 0, max_value: int = 1000000) -> int:
    try:
        val = int(_load_settings().get(name, default) or default)
    except Exception:
        val = default
    return max(min_value, min(max_value, val))


def _coerce_int(raw: Any, default: int) -> int:
    """Parse an int from a possibly-non-numeric value (settings.json that was
    hand-edited, or an external JSON field like a FlareSolverr 'status'). Never
    raises — falls back to `default` on any malformed input.

    Several call sites did `int(x.get(k, d) or d)`, which
    still raises ValueError when the stored value is a non-numeric string
    (e.g. "abc"). This helper makes those parses crash-proof.
    """
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return int(default)


class AIUsageBudget:
    """Small per-download budget so AI Assist cannot call paid APIs repeatedly by accident."""
    def __init__(self, max_calls: Optional[int] = None) -> None:
        self.max_calls = _get_ai_int_setting("ai_max_calls_per_download", 3, min_value=0, max_value=50) if max_calls is None else int(max_calls)
        self.calls = 0

    def can_call(self) -> bool:
        return self.max_calls <= 0 or self.calls < self.max_calls

    def consume(self, label: str = "AI") -> bool:
        if not self.can_call():
            logger.info(f"[{label}] AI call budget exhausted ({self.calls}/{self.max_calls})")
            return False
        self.calls += 1
        return True


def _ai_budget_consume(budget: Optional[AIUsageBudget], label: str) -> bool:
    if budget is None:
        return True
    return budget.consume(label)


def _clear_ai_plain_keys(settings: Optional[Dict[str, Any]] = None, provider: Optional[str] = None) -> Dict[str, Any]:
    """Remove plain-text AI keys from settings. If provider is None, remove all provider keys."""
    st = settings if settings is not None else _load_settings()
    providers = [_normalize_ai_provider(provider)] if provider else list(_VALID_AI_PROVIDERS)
    for p in providers:
        if p != "ollama":
            st[f"ai_api_key_{p}"] = ""
    st["ai_api_key"] = ""  # legacy Anthropic key
    return st


def _sanitize_ai_candidate_url(value: str) -> Optional[str]:
    """Whitelist AI URL/path outputs before urljoin/fetch."""
    v = (value or "").strip().strip('"\'')
    if not v or v.upper() in {"NONE", "NULL", "N/A", "[]"}:
        return None
    if len(v) > 600 or any(ord(ch) < 32 for ch in v):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", v) or v.startswith(("\\", "///")):
        return None
    lower = v.lower()
    if lower.startswith(("javascript:", "file:", "data:", "mailto:", "ftp:", "chrome:", "about:")):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", v) and not lower.startswith(("http://", "https://")):
        return None
    if v.startswith("//"):
        host = urlparse("https:" + v).hostname
        if host and _host_resolves_internal(host):
            return None
        return v if host else None
    # Block AI-suggested URLs that point at internal/loopback
    # addresses (prompt-injection SSRF). Mirrors the cyoa.cafe resolver guard.
    if lower.startswith(("http://", "https://")):
        host = urlparse(v).hostname
        if host and _host_resolves_internal(host):
            return None
    return v


def _host_is_internal(hostname: str) -> bool:
    """Return True if a hostname targets a loopback/link-local/private address.

    Defense-in-depth SSRF guard. The cyoa.cafe resolver and
    similar paths fetch URLs that originate from untrusted remote responses; a
    malicious entry could point at 127.0.0.1, 169.254.169.254 (cloud metadata),
    or RFC1918 ranges. Literal-IP hosts are classified directly; obvious local
    names are blocked by string match. We intentionally do NOT resolve DNS here
    (that would itself be a network call / DNS-rebind surface) — this is a cheap
    static screen layered on top of existing checks, not a complete egress
    firewall.
    """
    h = (hostname or "").strip().lower().rstrip(".")
    if not h:
        return True  # empty host → reject
    if h in ("localhost",) or h.endswith(".localhost") or h.endswith(".local"):
        return True
    # Strip IPv6 brackets if present.
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        import ipaddress as _ipaddr
        ip = _ipaddr.ip_address(h)
        return bool(
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        )
    except ValueError:
        # Not a literal IP address — a regular hostname. Allowed by this screen.
        return False


def _host_resolves_internal(hostname: str) -> bool:
    """Classify literal/local names and every currently resolved address.

    Resolution closes the common ``public-name -> 127.0.0.1/RFC1918`` bypass.
    A lookup failure is left to the normal request path so ordinary DNS errors
    retain their existing diagnostics.
    """
    if _host_is_internal(hostname):
        return True
    try:
        answers = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False
    return any(
        bool(sockaddr) and _host_is_internal(str(sockaddr[0]))
        for _family, _socktype, _proto, _canonname, sockaddr in answers
    )


# SSRF guard for the asset-fetch path.

# rev7 placed _host_is_internal() on the cyoa.cafe resolver and AI-output
# sanitizer, but the main asset fetch path (fetch_response / process_images /
# deep-scan) had no equivalent screen. A project.json or JS bundle from an
# untrusted site can therefore reference cross-origin internal addresses
# (127.0.0.1:<port>, 169.254.169.254 cloud metadata, RFC1918) and the
# downloader would dutifully fetch them — a genuine SSRF vector plus a wasted
# connect-timeout per bad URL.

# We must NOT break the legitimate "download my own CYOA from localhost" flow,
# so the rule is *cross-origin only*: an asset whose host is internal is blocked
# ONLY when it differs from the page's own origin. Same-origin internal fetches
# (localhost CYOA pulling its own assets) always pass. A global opt-out flag
# (--allow-internal-hosts) disables the screen entirely for power users.
_allow_internal_hosts = False


def _set_allow_internal_hosts(enabled: bool) -> None:
    """Process-local opt-out for the cross-origin internal-host SSRF screen."""
    global _allow_internal_hosts
    _allow_internal_hosts = bool(enabled)


def _ssrf_block_cross_origin(asset_url: str, base_url: str = "") -> bool:
    """Return True if *asset_url* should be blocked as a cross-origin SSRF target.

    Blocks only when ALL hold:
      • the opt-out flag is not set,
      • the asset host classifies as internal (_host_is_internal),
      • the asset origin differs from base_url's origin (cross-origin).

    Same-origin internal assets (localhost-hosted CYOA fetching its own files)
    are always allowed so the legitimate local-preview workflow keeps working.
    Malformed URLs are treated as not-blocked here; upstream sanitizers and the
    normal request machinery handle those.
    """
    if _allow_internal_hosts:
        return False
    try:
        a = urlparse(asset_url)
        if a.scheme not in ("http", "https"):
            return False  # non-http(s) handled elsewhere
        a_host = (a.hostname or "")
        if not a_host or not _host_resolves_internal(a_host):
            return False
        # Asset host is internal. Allow only if same-origin as the page.
        if base_url:
            b = urlparse(base_url)

            def _eff_port(p):
                if p.port is not None:
                    return p.port
                return {"http": 80, "https": 443}.get(p.scheme)

            if (b.scheme.lower(), (b.hostname or "").lower(), _eff_port(b)) == (
                a.scheme.lower(), (a.hostname or "").lower(), _eff_port(a)
            ):
                return False  # exact same-origin internal → legitimate
            # For internal targets we do NOT tolerate genuine port differences:
            # a different port on the same internal host is a different service
            # (e.g. localhost:8000 CYOA vs localhost:9 SSRF probe).
        return True
    except Exception:
        return False


def _get_ai_provider() -> str:
    st = _load_settings()
    return _normalize_ai_provider(st.get("ai_provider", "anthropic"))


def _get_ai_model(provider: Optional[str] = None) -> str:
    st = _load_settings()
    p = _normalize_ai_provider(provider or st.get("ai_provider", "anthropic"))
    m = (st.get("ai_model") or "").strip()
    if not m:
        return _default_ai_model(p)
    # If provider changed but the old provider's default model is still saved, move to the new provider default.
    other_defaults = {v for k, v in AI_PROVIDER_DEFAULT_MODEL.items() if k != p}
    return _default_ai_model(p) if m in other_defaults else m


def _plain_ai_key_setting(provider: Optional[str] = None) -> str:
    return f"ai_api_key_{_normalize_ai_provider(provider or _get_ai_provider())}"


def _keyring_username(provider: Optional[str] = None) -> str:
    return f"{_normalize_ai_provider(provider or _get_ai_provider())}_api_key"


def _read_ai_key_from_keyring(provider: Optional[str] = None) -> str:
    kr = _keyring_module()
    if kr is None:
        return ""
    user = _keyring_username(provider)
    try:
        val = kr.get_password(AI_KEYRING_SERVICE, user) or ""
        if not val and _normalize_ai_provider(provider or _get_ai_provider()) == "anthropic":
            # Backward compatibility with v7.3.3 keyring username.
            val = kr.get_password(AI_KEYRING_SERVICE, "anthropic_api_key") or ""
        return val
    except Exception as e:
        logger.debug(f"AI keyring read failed: {e}")
        return ""


def _write_ai_key_to_keyring(api_key: str, provider: Optional[str] = None) -> bool:
    kr = _keyring_module()
    if kr is None:
        return False
    user = _keyring_username(provider)
    try:
        if api_key:
            kr.set_password(AI_KEYRING_SERVICE, user, api_key)
        else:
            for username in {user, "anthropic_api_key" if _normalize_ai_provider(provider or _get_ai_provider()) == "anthropic" else user}:
                try:
                    kr.delete_password(AI_KEYRING_SERVICE, username)
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _write_ai_key_to_keyring (line 2067): %s", _ignored_exc)
        return True
    except Exception as e:
        logger.warning(f"AI keyring write failed: {e}")
        return False


def _resolve_ai_api_key(explicit_key: str = "", session_key: str = "", storage: Optional[str] = None,
                        provider: Optional[str] = None) -> str:
    """Resolve a provider-specific AI key without forcing it into settings.json.

    Priority:
      1. explicit_key passed by CLI/caller
      2. storage=session  → in-memory session_key
      3. storage=env      → provider env var, e.g. ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY
      4. storage=keyring  → provider-specific OS credential entry
      5. storage=plain    → provider-specific settings.json fallback

    Ollama/local does not require an API key.
    """
    p = _normalize_ai_provider(provider or _get_ai_provider())
    if p == "ollama":
        return ""
    if explicit_key:
        return explicit_key.strip()
    st = _load_settings()
    mode = _normalize_ai_key_storage(storage or st.get("ai_key_storage", "session"))
    if mode == "session":
        return (session_key or "").strip()
    if mode == "env":
        for env_name in _ai_env_vars(p):
            val = os.environ.get(env_name, "").strip()
            if val:
                return val
        return ""
    if mode == "keyring":
        return _read_ai_key_from_keyring(p).strip()
    if mode == "plain":
        return (st.get(_plain_ai_key_setting(p)) or (st.get("ai_api_key") if p == "anthropic" else "") or "").strip()
    return ""


def _clear_ai_api_key_storage(storage: Optional[str] = None, provider: Optional[str] = None, clear_all: bool = False) -> None:
    """Clear AI API keys.

    clear_all=True removes session-adjacent persistent copies from both plain settings
    and OS Credential Manager for the selected provider. Environment variables cannot
    be removed from inside the process, so they are only reported to the user.
    """
    st = _load_settings()
    p = _normalize_ai_provider(provider or st.get("ai_provider", "anthropic"))
    mode = _normalize_ai_key_storage(storage or st.get("ai_key_storage", "session"))
    if clear_all or mode == "plain":
        # Lock-safe re-read + clear + save so a concurrent settings write
        # elsewhere is not lost (Item 7 race fix parity).
        with _SETTINGS_LOCK:
            st2 = _load_settings()
            _clear_ai_plain_keys(st2, p)
            _save_settings(st2)
    if clear_all or mode == "keyring":
        _write_ai_key_to_keyring("", p)
    if mode == "env" and not clear_all:
        logger.info("AI key storage is environment-based; unset the environment variable to remove it.")


def _ai_key_status_text(storage: Optional[str] = None, session_key: str = "", provider: Optional[str] = None) -> str:
    st = _load_settings()
    p = _normalize_ai_provider(provider or st.get("ai_provider", "anthropic"))
    if p == "ollama":
        return f"{_ai_provider_label(p)} uses local Ollama and does not need an API key."
    mode = _normalize_ai_key_storage(storage or st.get("ai_key_storage", "session"))
    key = _resolve_ai_api_key(session_key=session_key, storage=mode, provider=p)
    if key:
        src = {"session": "session", "env": "/".join(_ai_env_vars(p)), "keyring": "OS Credential Manager", "plain": "settings.json"}.get(mode, mode)
        return f"{_ai_provider_label(p)} key found via {src}: {_mask_secret(key)}"
    if mode == "env":
        return f"No key found in {' or '.join(_ai_env_vars(p))}"
    if mode == "keyring":
        return "No key found in OS Credential Manager" if _keyring_module() else "keyring package not installed"
    if mode == "plain":
        return "No key saved in settings.json"
    return "Session key not set"


__all__ = [
    "AI_KEYRING_SERVICE", "_VALID_AI_KEY_STORAGE", "_VALID_AI_MODES",
    "_VALID_AI_PROVIDERS", "AI_PROVIDER_LABELS", "AI_PROVIDER_ENV_VARS",
    "AI_OPENAI_COMPAT_BASE", "AI_MODEL_OPTIONS", "AI_PROVIDER_DEFAULT_MODEL",
    "OLLAMA_DEFAULT_URL", "_normalize_ai_provider", "_ai_provider_label",
    "_ai_env_vars", "_ai_primary_env_var", "_ai_model_options",
    "_default_ai_model", "_normalize_ai_key_storage", "_normalize_ai_mode",
    "_ai_provider_needs_key", "_ai_is_available", "_ai_mode_allows",
    "_get_ai_int_setting", "_coerce_int", "AIUsageBudget",
    "_ai_budget_consume", "_clear_ai_plain_keys",
    "_sanitize_ai_candidate_url", "_host_is_internal", "_host_resolves_internal", "_allow_internal_hosts",
    "_set_allow_internal_hosts", "_ssrf_block_cross_origin",
    "_get_ai_provider", "_get_ai_model", "_plain_ai_key_setting",
    "_keyring_username", "_read_ai_key_from_keyring",
    "_write_ai_key_to_keyring", "_resolve_ai_api_key",
    "_clear_ai_api_key_storage", "_ai_key_status_text",
]
