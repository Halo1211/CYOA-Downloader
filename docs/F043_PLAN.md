# F-043 AI integration and F-044 plugins: migration plan (APPROVED 2026-11-20: A1–A4; P1 external JSON process)

Owner decision D4 approves porting AI integration and replacing Python plugins with external-process plugins. This plan splits the work into stages that can be checked one at a time.

## Python scope (audited 2026-11-20)

- `integrations/ai_core.py` (588 lines):
  - 9 providers (anthropic, openai, gemini, ollama, deepseek, qwen, groq, openrouter, custom), with aliases, labels, env vars, model presets and defaults.
  - Modes: `off`, `diagnostics`, `auto_fallback` (default), `aggressive_recovery`, with the mapping `_ai_mode_allows`.
  - `AIUsageBudget`: `ai_max_calls_per_download`, default 3, clamped 0..50, where 0 means unlimited.
  - Key resolution, in priority order: explicit, then session, env, keyring or plain. Plus clear-key and key-status text.
  - Candidate URL sanitization and SSRF helpers.
- `integrations/ai_calls.py` (439 lines):
  - `_ai_call`: one HTTP request and response parser per provider family. These are the Anthropic Messages API, the OpenAI Responses API, Gemini `generateContent`, Ollama `/api/generate`, and OpenAI-compatible `/chat/completions` (openrouter adds headers; custom reads its base URL from settings).
  - Three analyzers:
    - `_ai_detect_project_json`: discover Phase 5.
    - `_ai_analyze_js_for_assets`: deep scan, `aggressive_recovery` only.
    - `_ai_analyze_viewer_logic`: orchestrator diagnostics when no project is found.
  - `_extract_single_ai_url`.
- CLI: `--ai-key`, `--ai-provider`, `--ai-key-storage`, `--ai-model`, `--ai-mode`, `--ai-max-calls`, `--ai-max-html-chars`, `--ai-max-js-chars`, `--ai-clear-key`. The GUI has an AI settings panel.
- `integrations/plugins.py` (149 lines): an in-process registry of asset scanners and engine detectors, plus built-ins. It is called from the deep scan and the self-test.

**Privacy:** AI calls send page HTML or JS excerpts to the provider the user configured. Nothing is sent unless a key is available. The default key storage is `session`, so no call happens without `--ai-key`, an env, keyring or plain setting, or the Ollama provider. Rust keeps this gate.

## Stages

- **A1, pure core (`cyoa_integrations::ai`):**
  - What: provider, mode and storage normalization; labels, env vars and models; the `ai_mode_allows` mapping; budget; `extract_single_ai_url`; candidate sanitization; key resolution reusing the F-002 keyring (service `cyoa_downloader`, provider usernames like Python); clear-key and status text.
  - Check: a Python differential fixture (`fixtures/ai_core.json`) must match exactly.
- **A2, provider calls:**
  - What: request builders and response parsers for the 5 families over `cyoa-net` (proxy, VPN and DNS respected, bounded response size, cancellation).
  - Check: the requests Python sends (method, path, headers minus the secret value, JSON body) are captured by a local recording server; Rust must send identical requests to the same server, and parse the same canned responses identically.
  - No real provider is contacted in tests.
- **A3, analyzers and wiring:**
  - What: the three analyzers with the Python prompts byte-identical, wired at the three call sites; budget per download.
  - Check: wiremock end-to-end.
- **A4, CLI and GUI:**
  - What: the `--ai-*` flags with the same names and settings keys; `--ai-clear-key`; the GUI AI settings panel (provider, model, mode, key storage, budget).
  - Rule: secrets never go to logs or to `settings.json` unless `plain` storage is chosen explicitly.
- **P1, plugins (F-044), needs a design decision:**
  - Built-in scanners and detectors are ported natively.
  - User plugins become external executables that speak line-delimited JSON on stdin/stdout. The request is `{"kind":"asset_scanner","text":…,"url":…,"base_url":…,"ext":…}`; the response is `{"urls":[…]}`. Each plugin has a timeout, an output cap and no shell, and runs only from an explicit plugins folder.
  - Python plugin files cannot run in Rust. This difference is documented.

## Gates per stage

`cargo fmt`, `clippy -D warnings` and `cargo test`. The existing `e2e_parity`, `icc_parity` and `viewer_parity` must still pass. A Sol review is needed for A2 (network and secrets) and P1 (process execution).

## Questions for the owner

1. Approve stages A1 to A4 as described?
2. P1: is the external-process JSON protocol acceptable, or should user plugins be dropped, keeping only the built-ins?
