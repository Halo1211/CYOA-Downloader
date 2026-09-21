"""AI integration facade.

Provider/key-storage/SSRF helpers live in ``ai_core``, network calls/analyzers
live in ``ai_calls``, and GUI panel bodies are imported from the final GUI
behavior module.
"""

from __future__ import annotations

from ..gui.final_behaviors import _v25_ai_settings_panel, _v27_ai_settings_panel

# GUI compatibility helpers are now owned by GUI modules.
from ..gui.widgets import _v27_ai_provider_values

# Phase 33: provider-aware network calls/analyzers now live in ai_calls.
from .ai_calls import (
    _ai_analyze_js_for_assets,
    _ai_analyze_viewer_logic,
    _ai_call,
    _ai_detect_project_json,
    _extract_single_ai_url,
)
from .ai_core import (
    _VALID_AI_KEY_STORAGE,
    _VALID_AI_MODES,
    _VALID_AI_PROVIDERS,
    AI_KEYRING_SERVICE,
    AI_MODEL_OPTIONS,
    AI_OPENAI_COMPAT_BASE,
    AI_PROVIDER_DEFAULT_MODEL,
    AI_PROVIDER_ENV_VARS,
    AI_PROVIDER_LABELS,
    OLLAMA_DEFAULT_URL,
    AIUsageBudget,
    _ai_budget_consume,
    _ai_env_vars,
    _ai_is_available,
    _ai_key_status_text,
    _ai_mode_allows,
    _ai_model_options,
    _ai_primary_env_var,
    _ai_provider_label,
    _ai_provider_needs_key,
    _allow_internal_hosts,
    _clear_ai_api_key_storage,
    _clear_ai_plain_keys,
    _coerce_int,
    _default_ai_model,
    _get_ai_int_setting,
    _get_ai_model,
    _get_ai_provider,
    _host_is_internal,
    _host_resolves_internal,
    _keyring_username,
    _normalize_ai_key_storage,
    _normalize_ai_mode,
    _normalize_ai_provider,
    _plain_ai_key_setting,
    _read_ai_key_from_keyring,
    _resolve_ai_api_key,
    _sanitize_ai_candidate_url,
    _set_allow_internal_hosts,
    _ssrf_block_cross_origin,
    _write_ai_key_to_keyring,
)

__all__ = [
    "AI_KEYRING_SERVICE",
    "AI_MODEL_OPTIONS",
    "AI_OPENAI_COMPAT_BASE",
    "AI_PROVIDER_DEFAULT_MODEL",
    "AI_PROVIDER_ENV_VARS",
    "AI_PROVIDER_LABELS",
    "OLLAMA_DEFAULT_URL",
    "_VALID_AI_KEY_STORAGE",
    "_VALID_AI_MODES",
    "_VALID_AI_PROVIDERS",
    "AIUsageBudget",
    "_ai_analyze_js_for_assets",
    "_ai_analyze_viewer_logic",
    "_ai_budget_consume",
    "_ai_call",
    "_ai_detect_project_json",
    "_ai_env_vars",
    "_ai_is_available",
    "_ai_key_status_text",
    "_ai_mode_allows",
    "_ai_model_options",
    "_ai_primary_env_var",
    "_ai_provider_label",
    "_ai_provider_needs_key",
    "_allow_internal_hosts",
    "_clear_ai_api_key_storage",
    "_clear_ai_plain_keys",
    "_coerce_int",
    "_default_ai_model",
    "_extract_single_ai_url",
    "_get_ai_int_setting",
    "_get_ai_model",
    "_get_ai_provider",
    "_host_is_internal",
    "_host_resolves_internal",
    "_keyring_username",
    "_normalize_ai_key_storage",
    "_normalize_ai_mode",
    "_normalize_ai_provider",
    "_plain_ai_key_setting",
    "_read_ai_key_from_keyring",
    "_resolve_ai_api_key",
    "_sanitize_ai_candidate_url",
    "_set_allow_internal_hosts",
    "_ssrf_block_cross_origin",
    "_v25_ai_settings_panel",
    "_v27_ai_provider_values",
    "_v27_ai_settings_panel",
    "_write_ai_key_to_keyring",
]

