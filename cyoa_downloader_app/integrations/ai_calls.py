"""Provider-aware AI call and analyzer helpers extracted from legacy.py.

Phase 33 keeps the public behavior identical while moving the network-facing AI
helpers out of ``legacy.py``.  The lower-risk provider/key-storage helpers live
in ``ai_core.py``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from ..config.settings import _load_settings
from ..constants.assets import (
    IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS,
    SCRIPT_EXTENSIONS, STYLE_EXTENSIONS, FONT_EXTENSIONS,
)
from ..logging_setup import logger
from ..network.sessions import _get_shared_session
from .ai_core import (
    AI_OPENAI_COMPAT_BASE, OLLAMA_DEFAULT_URL, AIUsageBudget,
    _ai_budget_consume, _ai_is_available, _ai_mode_allows,
    _coerce_int, _default_ai_model, _get_ai_int_setting,
    _get_ai_model, _get_ai_provider, _normalize_ai_provider,
    _sanitize_ai_candidate_url,
)

def _extract_single_ai_url(text_value: str) -> Optional[str]:
    """Extract one URL/path from an AI response, rejecting unsafe schemes."""
    if not text_value:
        return None
    raw = text_value.strip().strip('"').strip("'")
    if raw.upper() in {"NONE", "NULL", "N/A", "NO", "NOT FOUND"}:
        return None
    if len(raw) > 600:
        return None
    scheme_match = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*):", raw)
    if scheme_match and scheme_match.group(1).lower() not in {"http", "https"}:
        return None
    pattern = r"(https?://[^\s\"'<>`]+|//[^\s\"'<>`]+|(?:\./|\.\./|/)[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+|[A-Za-z0-9._~/-]+\.(?:json|txt|zip)(?:\?[^\s\"'<>`]*)?)"
    m = re.search(pattern, raw)
    return m.group(1) if m else None

def _ai_detect_project_json(url: str, html_text: str,
                            api_key: str = "", provider: str = "",
                            ai_mode: str = "auto_fallback",
                            budget: Optional[AIUsageBudget] = None) -> Optional[str]:
    """AI-assisted project data locator. Provider-neutral.

    Returns a candidate URL only when AI mode permits recovery and the candidate
    passes strict URL/path sanitization.
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    if not _ai_mode_allows("project_detect", ai_mode) or not _ai_is_available(api_key, provider):
        return None
    if not _ai_budget_consume(budget, "AI project detect"):
        return None
    html_budget = _get_ai_int_setting("ai_max_html_chars", 8000, min_value=1000, max_value=50000)
    html_sample = html_text[:html_budget]
    result = _ai_call(
        api_key=api_key,
        provider=provider,
        prompt=(
            f"CYOA webpage at {url}.\nHTML (truncated):\n{html_sample}\n\n"
            "Find the URL where project.json data is loaded from. "
            "Look for fetch(), XHR, data-src, or script tags loading CYOA data. "
            "Reply ONLY the URL (absolute or relative). If not found, reply NONE."
        ),
        max_tokens=300,
        label="project detect",
    )
    candidate_raw = _sanitize_ai_candidate_url(_extract_single_ai_url(result or "") or "")
    if candidate_raw:
        candidate = candidate_raw if candidate_raw.startswith(("http://", "https://")) else urljoin(url, candidate_raw)
        logger.info(f"[AI detect] project candidate → {candidate}")
        return candidate
    return None

def _ai_call(api_key: str, prompt: str, max_tokens: int = 1024,
             system: str = "", label: str = "ai", model: str = "",
             provider: str = "") -> Optional[str]:
    """Provider-aware low-level AI call. Returns response text or None.

    Supported providers:
      - anthropic: Anthropic Messages API
      - openai: OpenAI Responses API
      - gemini: Google Gemini generateContent API
      - ollama: local Ollama /api/generate
      - deepseek/qwen/groq/openrouter/custom: OpenAI-compatible /chat/completions
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    model = (model or _get_ai_model(provider)).strip() or _default_ai_model(provider)
    if provider != "ollama" and not api_key:
        return None
    try:
        session = _get_shared_session(use_cf=False)
        if provider == "anthropic":
            body: Dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                body["system"] = system
            r = session.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json",
                         "x-api-key": api_key,
                         "anthropic-version": "2023-06-01"},
                json=body,
                timeout=60,
            )
            if r.status_code != 200:
                logger.debug(f"[{label}] Anthropic API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            return "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text").strip() or None

        if provider == "openai":
            input_payload: List[Dict[str, str]] = []
            if system:
                input_payload.append({"role": "system", "content": system})
            input_payload.append({"role": "user", "content": prompt})
            body = {"model": model, "input": input_payload, "max_output_tokens": max_tokens}
            r = session.post(
                "https://api.openai.com/v1/responses",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=60,
            )
            if r.status_code != 200:
                logger.debug(f"[{label}] OpenAI API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            if data.get("output_text"):
                return str(data["output_text"]).strip() or None
            parts: List[str] = []
            for item in data.get("output", []) or []:
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("text"):
                        parts.append(str(c.get("text")))
            return "".join(parts).strip() or None

        if provider == "gemini":
            import urllib.parse as _up
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{_up.quote(model, safe='')}:generateContent?key={_up.quote(api_key, safe='')}"
            body: Dict[str, Any] = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            }
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            r = session.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=60)
            if r.status_code != 200:
                logger.debug(f"[{label}] Gemini API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            parts: List[str] = []
            for cand in data.get("candidates", []) or []:
                for part in cand.get("content", {}).get("parts", []) or []:
                    if part.get("text"):
                        parts.append(str(part["text"]))
            return "".join(parts).strip() or None

        if provider == "ollama":
            st = _load_settings()
            base = (st.get("ollama_url") or OLLAMA_DEFAULT_URL).rstrip("/")
            body = {
                "model": model,
                "prompt": (system + "\n\n" if system else "") + prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            }
            r = session.post(base + "/api/generate", headers={"Content-Type": "application/json"}, json=body, timeout=120)
            if r.status_code != 200:
                logger.debug(f"[{label}] Ollama API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            return str(data.get("response", "")).strip() or None

        if provider in ("deepseek", "qwen", "groq", "openrouter", "custom"):
            # OpenAI-compatible /chat/completions. Groq and OpenRouter use fixed
            # base URLs; DeepSeek/Qwen/Groq/OpenRouter are fixed presets; "custom" reads ai_custom_base_url from settings so any
            # OpenAI-compatible endpoint (LM Studio, vLLM, etc.) can be used
            # without code changes.
            if provider == "custom":
                base = (_load_settings().get("ai_custom_base_url") or "").strip().rstrip("/")
                if not base:
                    logger.warning(f"[{label}] custom AI provider needs ai_custom_base_url")
                    return None
            else:
                base = AI_OPENAI_COMPAT_BASE[provider]
            messages: List[Dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            _temp = _load_settings().get("ai_temperature")
            if _temp is not None:
                try:
                    body["temperature"] = float(_temp)
                except (TypeError, ValueError) as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _ai_call (line 3795): %s", _ignored_exc)
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {api_key}"}
            if provider == "openrouter":
                # OpenRouter recommends identifying the calling app.
                headers["HTTP-Referer"] = "https://github.com/cyoa-downloader"
                headers["X-Title"] = "CYOA Downloader"
            r = session.post(base + "/chat/completions", headers=headers, json=body, timeout=60)
            if r.status_code != 200:
                logger.debug(f"[{label}] {provider} API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            try:
                return str(data["choices"][0]["message"]["content"]).strip() or None
            except (KeyError, IndexError, TypeError):
                return None

        logger.warning(f"[{label}] Unsupported AI provider: {provider}")
        return None
    except Exception as e:
        logger.debug(f"[{label}] error: {e}")
    return None

def _ai_analyze_js_for_assets(
    js_files: Dict[str, str],
    base_url: str,
    api_key: str = "",
    provider: str = "",
    ai_mode: str = "aggressive_recovery",
    budget_obj: Optional[AIUsageBudget] = None,
) -> List[str]:
    """AI-assisted JS analysis to discover asset URLs that BFS scan missed.

    Sends truncated JS content to Claude and asks it to find:
    - Image/audio/video URLs or paths
    - Dynamic import() targets
    - Lazy-loaded chunk names
    - Data URLs or fetch() targets
    - Asset arrays, maps, or objects

    Returns list of candidate URLs (absolute).
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    if not js_files or not _ai_mode_allows("asset_scan", ai_mode) or not _ai_is_available(api_key, provider):
        return []
    if not _ai_budget_consume(budget_obj, "AI asset scan"):
        return []

    # Build a compact JS sample: filename + first N chars of each file
    # Budget is user-configurable from AI settings.
    budget = _coerce_int(_load_settings().get("ai_max_js_chars", 14000), 14000)
    per_file = max(800, budget // max(len(js_files), 1))
    samples = []
    for fname, content in js_files.items():
        snippet = content[:per_file]
        samples.append(f"--- {fname} ({len(content)} chars) ---\n{snippet}")
    combined = "\n\n".join(samples)
    if len(combined) > budget:
        combined = combined[:budget]

    system_prompt = (
        "You are an expert JavaScript reverse engineer analyzing CYOA "
        "(Choose Your Own Adventure) web applications. Your task is to "
        "extract asset references from minified JS bundles."
    )
    user_prompt = (
        f"Base URL: {base_url}\n\n"
        f"JS files (truncated):\n{combined}\n\n"
        "Analyze these JS files and extract ALL asset URLs/paths you can find:\n"
        "1. Image paths (.png, .jpg, .jpeg, .webp, .gif, .svg, .avif)\n"
        "2. Audio paths (.mp3, .ogg, .wav, .m4a, .flac, .aac)\n"
        "3. Video paths (.mp4, .webm)\n"
        "4. Dynamic import() or lazy-loaded chunk filenames (.js, .mjs)\n"
        "5. fetch() or XHR target URLs\n"
        "6. CSS file references (.css)\n"
        "7. Font file references (.woff, .woff2, .ttf, .otf)\n"
        "8. JSON data files\n\n"
        "Look for: string literals, template literals, array/object definitions, "
        "concatenated paths, Vite/Webpack chunk maps, asset manifests.\n\n"
        "Reply with ONLY a JSON array of relative or absolute URL strings. "
        "Example: [\"/assets/bg.webp\", \"./chunks/lazy-DxMS06T8.js\", \"music/theme.mp3\"]\n"
        "If nothing found, reply: []"
    )

    result = _ai_call(
        api_key=api_key,
        prompt=user_prompt,
        system=system_prompt,
        max_tokens=2000,
        label="AI asset scan",
        provider=provider,
    )
    if not result:
        return []

    # Parse JSON array from response
    try:
        # Strip markdown fences if present
        cleaned = re.sub(r'^```(?:json)?\s*', '', result.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        candidates = json.loads(cleaned)
        if not isinstance(candidates, list):
            return []
        # Resolve to absolute URLs
        resolved = []
        for c in candidates:
            if not isinstance(c, str) or not c.strip():
                continue
            c = _sanitize_ai_candidate_url(c)
            if not c:
                continue
            # Only accept likely web assets/data endpoints. MIME is validated again at download time.
            path_l = urlparse(c).path.lower()
            if path_l and not path_l.endswith(tuple(IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | SCRIPT_EXTENSIONS | STYLE_EXTENSIONS | FONT_EXTENSIONS | {".json", ".html", ".htm", ".svg"})):
                # Keep extensionless relative fetch targets, but skip obvious non-assets.
                if "." in os.path.basename(path_l):
                    continue
            if c.startswith(("http://", "https://")):
                resolved.append(c)
            else:
                resolved.append(urljoin(base_url, c))
        logger.info(f"[AI asset scan] {len(resolved)} candidate(s) from {len(js_files)} JS file(s)")
        return resolved
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(f"[AI asset scan] JSON parse error: {e}")
        return []

def _ai_analyze_viewer_logic(
    html_text: str,
    js_samples: Dict[str, str],
    url: str,
    api_key: str = "",
    provider: str = "",
    ai_mode: str = "diagnostics",
    budget_obj: Optional[AIUsageBudget] = None,
) -> Dict[str, Any]:
    """AI-assisted analysis of how a CYOA viewer loads and structures data.

    Returns dict with insights:
    - data_source: how/where the viewer loads CYOA data
    - asset_base: base path for assets
    - viewer_type: detected viewer type
    - suggestions: list of recommended actions
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    if not _ai_mode_allows("diagnostics", ai_mode) or not _ai_is_available(api_key, provider):
        return {}
    if not _ai_budget_consume(budget_obj, "AI viewer analysis"):
        return {}

    # Build compact sample
    html_limit = min(4000, _get_ai_int_setting("ai_max_html_chars", 8000, min_value=1000, max_value=50000))
    js_limit = min(3000, _get_ai_int_setting("ai_max_js_chars", 14000, min_value=1000, max_value=100000))
    html_sample = html_text[:html_limit]
    js_sample_parts = []
    for fname, content in list(js_samples.items())[:3]:
        js_sample_parts.append(f"--- {fname} ---\n{content[:js_limit]}")
    js_combined = "\n\n".join(js_sample_parts)

    result = _ai_call(
        api_key=api_key,
        system=(
            "You are an expert at analyzing CYOA (Choose Your Own Adventure) "
            "web viewers. Analyze the HTML and JS to understand the viewer architecture."
        ),
        prompt=(
            f"URL: {url}\n\nHTML:\n{html_sample}\n\nJS:\n{js_combined}\n\n"
            "Analyze this CYOA viewer and reply ONLY as JSON:\n"
            "{\n"
            '  "data_source": "how the viewer loads CYOA data (fetch, inline, script tag, etc)",\n'
            '  "asset_base": "base URL/path for images and assets",\n'
            '  "viewer_type": "icc_plus|icc_remix|react_custom|vue_custom|other",\n'
            '  "chunk_pattern": "pattern for lazy-loaded JS chunks if any",\n'
            '  "suggestions": ["list of download strategy recommendations"]\n'
            "}"
        ),
        max_tokens=800,
        label="AI viewer analysis",
        provider=provider,
    )
    if not result:
        return {}
    try:
        cleaned = re.sub(r'^```(?:json)?\s*', '', result.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        obj = json.loads(cleaned)
        if not isinstance(obj, dict):
            return {}
        allowed = {"data_source", "asset_base", "viewer_type", "chunk_pattern", "suggestions"}
        clean: Dict[str, Any] = {k: obj.get(k) for k in allowed if k in obj}
        if "suggestions" in clean and not isinstance(clean["suggestions"], list):
            clean["suggestions"] = [str(clean["suggestions"])]
        return clean
    except (json.JSONDecodeError, ValueError):
        return {}
