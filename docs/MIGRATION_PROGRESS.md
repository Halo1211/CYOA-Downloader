# Migration progress (primary state document)

**Session 8 (cont., 2026-11-20):** F-051 diagnostics (CLI + GUI), F-036 XLSX/XLS import, F-052 update checks + GUI Updates/Batch Check Center, and the GUI rebuilt to the Python layout and palette (titlebar, mode sidebar, Input section, badges, action rows, LOG; Download All flow). 321 Rust tests pass; fmt/clippy clean. GUI not compared visually with Python (screen capture denied) — owner check requested.

**Session 8 (2026-11-20):** Codex (Luna/Sol) unavailable until 2026-11-21; Claude worked alone, Ollama MiMo used for one port (rejected: did not compile) and one review (no findings). Done: GUI CYOA Manager library panel (F-038); F-037 V4 modernizer + CLI `--modernize-library` + ICC website hook (Python-identical fixture and CLI run); F-037 V5 viewer registration + GUI "Viewers…" panel (Python-identical fixture). 314 Rust tests pass; fmt and clippy (-D warnings) clean; e2e_parity 6/6, viewer_parity OK. Not run manually in the GUI. Pending: Sol review (V3 modules, library serve path, modernizer.rs, viewer_registry.rs), then F-028 browser/CDP.

**Session 7 (2026-11-19): F-037 offline viewers V1–V3 + consumers.** 306+ Rust tests pass; fmt and clippy (-D warnings) clean; `e2e_parity.sh` 6/6 OK; new `rust/scripts/viewer_parity.sh` 5/5 identical to Python (manual viewer in embed/zip/both, auto selection + legacy index merge on a classic ICC page).
- Plan: `docs/F037_PLAN.md` (approved V1–V3, RAR = system tar). V4 (modernizer, `--modernize-library`) and V5 (GUI viewer panel) still need approval.
- V1 registry (`cyoa_integrations::offline_viewers`), V2 injection (`cyoa_engine::offline_viewer`), preserved publisher index (`cyoa_engine::preserved_index`), V3 wiring in `run.rs` (settings `offline_viewer_json_enabled` / `offline_viewer_preferred_id`, CLI + GUI), embed/both `audio/` merge into the output folder (was missing).
- Unblocked: itch.io HTML5 ZIP offline folders (`cyoa_engine::itch_html5`, CLI `--itch*` now prepares them) and the CYOA Manager serve folder (`cyoa_engine::manager_serve`; GUI library panel still missing).
- Python fixtures: `fixtures/viewers.json`, `viewer_inject.json` (27 trees), `preserved_index.json` (76 cases), `itch_html5.json` (10 steps), `manager_serve.json` (5 steps); all byte-identical.
- Sol reviewed V2 (6 findings, all fixed: bounded per-member RAR extraction with deadline, link refusal, localization budgets, fetcher contract). Sol's V3 review stopped on the ChatGPT usage limit (resets 2026-11-21); Claude fixed the casefold finding it was tracing and added byte-bounded HTML5 member reads. Re-run `sol_review` on itch_html5.rs / manager_serve.rs / preserved_index.rs after the reset.
- Agent note: Luna produced partial/non-compiling work twice (V2 skeleton, preserved_index); Claude wrote those modules. Heredocs mangle `
`/`
` escapes: use the Edit tool for Rust/Python sources.

**Session 6 (2026-11-18), latest commit `05d58da`:** 219 Rust tests pass; fmt and clippy (-D warnings) are clean; `e2e_parity.sh` reports 6 OK lines (now byte-exact, and it fails when a file is missing).
- F-016 proxy: core, CLI, GUI. Redaction and validation checked against Python.
- F-017 custom DNS: UDP/TCP/DoT/DoH, a cache, and fallback. Wired into `PolicyResolver` with the SSRF check kept. CLI and GUI done. Python differential fixture. Sol review findings fixed.
- F-018 VPN guard: enforced at the fetch boundary. CLI and GUI done. Name matching matches Python. Not tested: live Windows enumeration.
- Reports: `write_asset_failure_summary` parity, CRLF `.txt` on Windows, and `cyoa_downloader.log` (rotation and redaction).
- Infrastructure: in hybrid-team `bridge.py`, subprocess stdin is DEVNULL (fixes 30-minute hangs). EOL-only false positives are ignored. Luna's isolated mode is verified.
- Owner action: the `active.json` recovery marker blocks Codex tasks (see NEXT_SESSION_PROMPT).

**Current phase (session 4, 2026-11-18):**
- Rust download engine, CLI MVP and GUI (A–C) work end to end.
- Output is byte-identical to Python on the fixture site (`bash rust/scripts/e2e_parity.sh`) and on a 300-image benchmark.
- 154 Rust tests pass; fmt and clippy clean.
- See `docs/NEXT_SESSION_PROMPT.md` for the exact next tasks, and `OVERNIGHT_REPORT.md` "session 4" for details.

**Not complete (honest scope):**
- Website/ICC/cyoap-vue/archive modes
- browser, Cloudflare and cookie fallbacks
- gallery-dl and yt-dlp
- proxy, DNS and VPN
- `-L` batch import
- payload spooling (memory)
- the Python GUI's extra features
- release packaging

---

**Current phase (06:00):**
- Stages 1–2 complete.
- Stage 3 core complete: admission, retry, throttle, policy, headers and the HTTP client. Still open: proxy, DNS, VPN, Cloudflare, response metadata.
- Stage 4 in progress: `parse.py` and the pure discovery helpers are done.
- **94 Rust tests pass.** Next steps: OVERNIGHT_REPORT.md → "Remaining Stage 4 work".

**Earlier (03:20):** Stages 1 and 2 complete.

Feature status:
- **TESTED** (golden or byte parity against Python): F-001…F-006, F-008 (cache), F-010 (resume), F-011 (checkpoints, interoperable DB naming/schema/payload), F-009 (history record path; the background metadata probe is pending the network stage).
- **Sol-reviewed:** F-003 and F-004.
- **Not yet reviewed:** cache and checkpoints still need a Sol security pass for the link guards.

Rust tests: 59 in storage plus 7 in core, all passing; clippy -D warnings is clean.

Resume procedure: `cd rust; cargo test --offline`. Regenerate fixtures with `python rust/fixtures/export_*.py`, run from the repo root. Pre-fetch any new crate with `cargo add` + `cargo fetch` before delegating (agents run `--offline`).

---
**Earlier state:** Phase 1 audit and Phase 2 architecture proposal complete.
**Last updated:** 2026-11-18 · Branch `migration/python-to-rust` · No Rust code exists yet.

## Completed
- Phase 0 environment validation (ENVIRONMENT.md).
- Agent smoke tests (AGENT_TASKS A-000).
- Read-only audit by Luna, Sol, MiMo and Qwen; Claude verified the key claims.
- Python baseline: 1633 passed / 21 skipped.
- Audit documents: PROJECT_AUDIT, FEATURE_PARITY, DEPENDENCY_MAP, ARCHITECTURE, KNOWN_ISSUES, MIGRATION_PLAN, BENCHMARKS, AGENT_TASKS, SECURITY_REVIEW, ENVIRONMENT.

## Modified files
- Only new files under `docs/`. No source files changed. No commit yet.

## Owner decisions (approved 2026-11-18: "follow the recommendations, keep every feature, Rust replacements allowed, results identical or better")
- D1 APPROVED: egui/eframe, after the prototype gate.
- D2 APPROVED: workspace under `rust/` with the proposed crate split.
- D3 CHANGED: **no feature is dropped.** Playwright and Selenium are both replaced by one CDP browser backend (chromiumoxide), which also provides the Selenium fallback behavior. browser-cookie3 becomes a native Rust cookie import (Chromium DPAPI/AES-GCM, Firefox SQLite) where the OS allows it. Cloudflare is handled through the CDP browser session hand-off and FlareSolverr. Access controls are never bypassed.
- D4 APPROVED (port): AI integration is ported. Python plugins get a Rust equivalent: external-process plugins speaking a JSON protocol.
- D5 APPROVED: secrets live in the keyring by default; plaintext only on explicit opt-in. The legacy plaintext token is migrated into the keyring.
- D6 APPROVED: implementation stages proceed sequentially. Each stage gets diff inspection and the quality gates.
- D7 APPROVED: MiMo and Qwen are limited to narrow tasks that can be checked.

## Next recommended action
After approval: Stage 0 (workspace skeleton plus a Python golden-fixture exporter) done by Luna, then a diff inspection and the quality gates.
