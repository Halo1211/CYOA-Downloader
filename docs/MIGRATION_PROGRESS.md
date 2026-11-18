# Migration progress (primary state document)

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
