"""Export Python AI core helper outputs (F-043 A1 parity).

Run from the repo root: python fixtures/export_ai_core.py
Writes fixtures/ai_core.json. No network or keyring access: settings are
patched in memory and candidate hosts are literal IPs or reserved names.
"""

import json
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "old"))

from cyoa_downloader_app.integrations import ai_calls, ai_core  # noqa: E402

PROVIDERS = ["", "Anthropic", "claude", "anthropic-claude", "open ai", "GPT", "google", "Google Gemini",
             "local", "ollama-local", "deep-seek", "dashscope", "aliyun", "alibaba", "groq",
             "openrouter", "custom", "qwen_dashscope", "bogus", "  OpenAI  "]
MODES = ["", "off", "Diagnostics", "diagnostics-only", "auto", "aggressive", "aggressive-recovery",
         "auto_fallback", "nope"]
STORAGES = ["", "session", "Environment Variable", "os credential manager", "credential-manager",
            "os_keyring", "settings", "settings.json", "plain settings json", "session only", "keyring",
            "plain", "env", "weird"]
KINDS = ["diagnostics", "project_detect", "asset_scan", "other"]
URL_TEXTS = ["", "NONE", "  null ", "n/a", "No", "not found", "https://a.test/p.json",
             "'https://a.test/p.json'", "The data is at https://a.test/data/project.json, enjoy.",
             "./project.json", "../data/x.json?v=2", "/abs/path/project.json", "project.json",
             "data/sub/project.txt", "javascript:alert(1)", "file:///etc/passwd", "ftp://x/y.json",
             "//cdn.test/x.json", "x" * 601, "see `https://a.test/q.json` here",
             "data: https://a.test/x.json", "http://h/a b.json"]
CANDIDATES = ["", "NONE", "[]", "https://a.test/p.json", "http://127.0.0.1/x.json",
              "http://10.0.0.5/p.json", "https://[::1]/p.json", "http://localhost/p.json",
              "https://foo.local/p.json", "//127.0.0.1/x.json", "//cdn.example.invalid/x.json",
              "javascript:alert(1)", "file:///c:/x", "data:text/plain,hi", "mailto:a@b",
              "C:\\x\\y.json", "\\\\srv\\share", "///x", "./p.json", "p.json", "a\tb",
              "https://a.test/with space.json", "chrome://settings", "about:blank",
              "custom-scheme:x", "https://a.test:99999/p.json", "http://169.254.169.254/latest",
              "https://example.invalid/p.json", "x" * 601]

out = {}
out["normalize_provider"] = {p: ai_core._normalize_ai_provider(p) for p in PROVIDERS}
out["normalize_mode"] = {m: ai_core._normalize_ai_mode(m) for m in MODES}
out["normalize_key_storage"] = {s: ai_core._normalize_ai_key_storage(s) for s in STORAGES}
out["mode_allows"] = {f"{k}|{m}": ai_core._ai_mode_allows(k, m) for k in KINDS for m in MODES if m}
providers = sorted(ai_core._VALID_AI_PROVIDERS)
out["providers"] = {
    p: {
        "label": ai_core._ai_provider_label(p),
        "env_vars": ai_core._ai_env_vars(p),
        "primary_env_var": ai_core._ai_primary_env_var(p),
        "models": ai_core._ai_model_options(p),
        "default_model": ai_core._default_ai_model(p),
        "needs_key": ai_core._ai_provider_needs_key(p),
        "plain_key_setting": ai_core._plain_ai_key_setting(p),
        "keyring_username": ai_core._keyring_username(p),
        "available_without_key": ai_core._ai_is_available("", p),
        "available_with_key": ai_core._ai_is_available(" k ", p),
    }
    for p in providers
}

model_cases = []
for provider in ["anthropic", "deepseek", "groq", "openai", "ollama"]:
    for saved in ["", "  ", "my-model", "deepseek-chat", "llama-3.3-70b-versatile", "gpt-6-luna",
                  "claude-sonnet-5", "qwen3.5"]:
        with mock.patch.object(ai_core, "_load_settings", return_value={"ai_provider": provider, "ai_model": saved}):
            model_cases.append({"provider": provider, "saved": saved, "model": ai_core._get_ai_model(provider)})
out["get_model"] = model_cases

out["extract_single_ai_url"] = [{"in": t, "out": ai_calls._extract_single_ai_url(t)} for t in URL_TEXTS]
out["sanitize_candidate"] = [{"in": c, "out": ai_core._sanitize_ai_candidate_url(c)} for c in CANDIDATES]

int_cases = []
for raw in [None, 0, 5, "7", "abc", 999999, -3, 2.9, ""]:
    st = {} if raw is None else {"ai_max_html_chars": raw}
    with mock.patch.object(ai_core, "_load_settings", return_value=st):
        int_cases.append({"raw": raw, "value": ai_core._get_ai_int_setting(
            "ai_max_html_chars", 8000, min_value=1000, max_value=50000)})
out["int_setting"] = int_cases

budget_cases = []
for max_calls in [0, 1, 3, -1]:
    b = ai_core.AIUsageBudget(max_calls)
    budget_cases.append({"max": max_calls, "seq": [b.consume("x") for _ in range(5)]})
for raw in [None, 0, 2, 99, "abc", -5]:
    st = {} if raw is None else {"ai_max_calls_per_download": raw}
    with mock.patch.object(ai_core, "_load_settings", return_value=st):
        budget_cases.append({"setting": raw, "max_calls": ai_core.AIUsageBudget().max_calls})
out["budget"] = budget_cases

key_cases = []
env = {"ANTHROPIC_API_KEY": " envA ", "GOOGLE_API_KEY": "envG2", "OPENAI_API_KEY": "envO"}
settings = {"ai_api_key": " legacyA ", "ai_api_key_openai": "plainO", "ai_api_key_gemini": ""}
for provider in ["anthropic", "openai", "gemini", "custom", "ollama", "qwen"]:
    for storage in ["session", "env", "plain"]:
        for explicit, session in [("", ""), ("", " sess "), (" expl ", "sess")]:
            with mock.patch.object(ai_core, "_load_settings", return_value=dict(settings, ai_key_storage=storage)), \
                    mock.patch.dict(os.environ, env, clear=False):
                for name in ["GEMINI_API_KEY", "CUSTOM_AI_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
                             "ALIYUN_API_KEY"]:
                    os.environ.pop(name, None)
                key_cases.append({"provider": provider, "storage": storage, "explicit": explicit,
                                  "session": session,
                                  "key": ai_core._resolve_ai_api_key(explicit, session, storage, provider)})
out["resolve_key"] = {"env": env, "settings": settings, "cases": key_cases}

path = ROOT / "rust" / "fixtures" / "ai_core.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", path)
