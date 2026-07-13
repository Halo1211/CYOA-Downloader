"""AI integration facade.

Provider/key-storage/SSRF helpers live in ``ai_core``, network calls/analyzers
live in ``ai_calls``, and GUI panel bodies are imported from the final GUI
behavior module.
"""

from __future__ import annotations

from .ai_core import (
    AI_KEYRING_SERVICE, _VALID_AI_KEY_STORAGE, _VALID_AI_MODES,
    _VALID_AI_PROVIDERS, AI_PROVIDER_LABELS, AI_PROVIDER_ENV_VARS,
    AI_OPENAI_COMPAT_BASE, AI_MODEL_OPTIONS, AI_PROVIDER_DEFAULT_MODEL,
    OLLAMA_DEFAULT_URL, _normalize_ai_provider, _ai_provider_label,
    _ai_env_vars, _ai_primary_env_var, _ai_model_options, _default_ai_model,
    _normalize_ai_key_storage, _normalize_ai_mode, _ai_provider_needs_key,
    _ai_is_available, _ai_mode_allows, _get_ai_int_setting, _coerce_int,
    AIUsageBudget, _ai_budget_consume, _clear_ai_plain_keys,
    _sanitize_ai_candidate_url, _host_is_internal, _host_resolves_internal, _allow_internal_hosts,
    _set_allow_internal_hosts, _ssrf_block_cross_origin, _get_ai_provider,
    _get_ai_model, _plain_ai_key_setting, _keyring_username,
    _read_ai_key_from_keyring, _write_ai_key_to_keyring,
    _resolve_ai_api_key, _clear_ai_api_key_storage, _ai_key_status_text,
)
# Phase 33: provider-aware network calls/analyzers now live in ai_calls.
from .ai_calls import (
    _extract_single_ai_url, _ai_detect_project_json, _ai_call,
    _ai_analyze_js_for_assets, _ai_analyze_viewer_logic,
)

# GUI compatibility helpers are now owned by GUI modules.
from ..gui.widgets import _v27_ai_provider_values
from ..gui.final_behaviors import _v25_ai_settings_panel
from ..gui.final_behaviors import _v27_ai_settings_panel

__all__ = [name for name in globals() if not name.startswith("__")]

