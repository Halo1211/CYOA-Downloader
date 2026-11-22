# Migration progress (primary state document)

**Current status (2026-11-22):** Rust is the primary application; the Python reference remains runnable under `old/`. All numbered migration features have Rust implementations. Remaining behavior differences and checks not performed are listed in `docs/FEATURE_PARITY.md` and `docs/TEST_RESULTS.md`. The only intentionally omitted solver is the optional cloudscraper path. Run Cargo commands from the repository root; crates are in `crates/`. See `README.md` for current commands.

**Repository layout:** parity scripts are in `scripts/`, fixtures in `fixtures/`, and Python reference sources, tests, and tooling are in `old/`.

**Release status (2026-11-22):** GitHub Actions run [44959993152](https://github.com/Halo1211/CYOA-Downloader/actions/runs/44959993152) built the Rust binaries successfully, then failed in `scripts/third_party_notices.py` because `cargo metadata --offline` exited 101; artifact upload and release publication were skipped. The license packaging now uses locked online metadata and includes a companion license archive. This workflow correction has not yet run on GitHub; the existing `v1.1.6` tag still points to the failed run revision.

**CI follow-up (2026-11-22):** Run 44987139831 passed all main Rust unit tests but failed the viewer injection parity fixture because the generated .txt report used LF on Linux while the Python fixture uses CRLF. The fixture comparison now canonicalizes line endings as well as masking timestamps; full Windows cargo test --locked passes locally. The feature-enabled dependency graph was also recorded in Cargo.lock so the Windows release's locked build remains reproducible.

**Session 9 (cont., 2026-11-22): last gaps ported.** F-045 browser cookies (DL-15), yt-dlp browser-cookie retry, Retry Assets route continuation, bundled-viewer registration. 375 tests pass; fmt and clippy clean. Nothing remains unported except the obsolete cloudscraper solver (FlareSolverr instead).

**Session 9 (2026-11-21): security hardening and real-site validation.** Browser relay (SSRF + proxy credentials), four Sol review rounds with all findings fixed (`docs/SECURITY_REVIEW.md`), real-site tests on 12 sites plus AI/audio/viewers/cheat/FlareSolverr/itch.io (`docs/TEST_RESULTS.md`), native TLS on Windows (DL-10), urllib3-style mirror retries (DL-11), origin-limited browser preference (DL-12), automatic recovery (DL-13), file-based plugin I/O (DL-14). Gates: fmt, clippy -D warnings, 369 tests. Commits 0550b3e, dc0f967 and the docs commit; not pushed.

**Session 8 (cont. 4, 2026-11-20): F-049 Settings dashboard done.** Python `_settings_dashboard_panel` layout (Download/Network/Integrations/Viewers/Maintenance + keyword search) with Python-style cards (badges, framed surfaces, accent buttons, switches): cookies, itch key, archive policy, feature switches, AI Assist, CYOA Manager, Cloudflare/FlareSolverr (Save/Test/Clear sessions), gallery-dl config, Discord token test, viewers, settings tools, image cache. GUI runs honour saved switches. 361 Rust tests pass. Not compared visually (screen capture unavailable) — owner check requested.

**Session 8 (cont. 3, 2026-11-20): F-043 AI integration (A1–A4) and F-044 plugins done** (owner approved plan `docs/F043_PLAN.md`). AI core, provider calls and analyzers are identical to Python on three fixtures (core helpers, 276 recorded provider requests/replies, analyzer prompts/parsing); wired into discovery Phase 5, no-project diagnostics and the deep-scan AI round; CLI `--ai-*` (settings persistence identical to Python) and GUI AI Assist panel. Plugins run as external JSON processes (`docs/PLUGINS.md`). Also: F-028 runtime-capture metadata bounded per page. Gates: fmt/clippy clean; 358 Rust tests pass (3 ignored); e2e 6/6, icc 15/15, viewer 5/5. Sol review of F-028 attempted: Codex usage limit until 2026-11-21 11:19 — still pending, now also for `ai_calls.rs`/`ai_run.rs` (network + secrets) and `plugins.rs` (process execution).

**Session 8 (cont. 2, 2026-11-20): F-028 browser strategy done (largest remaining feature).** `cyoa_engine::browser` drives an installed Chrome/Edge/Brave over CDP (chromiumoxide 0.9.1, D3): reusable browser transport, headless fetch (Selenium step replaced by an in-page credentials fetch), runtime capture (scroll, safe interactions, request blocking, response reuse). Wired into the mirror (`_fetch` browser retry, headless entry/route HTML), ICC and Pure Website Browser strategy (manifest `runtime`), and the project-image headless fallback (`selenium_enabled`). CLI/GUI accept `browser`. `cyoa_net::proxy::BrowserRoute` + `FetchClient::browser_session_state/vpn_satisfied`. Gates: fmt, check, clippy -D warnings clean; 340 Rust tests pass (3 ignored); `e2e_parity.sh` 6/6; `viewer_parity.sh` 5/5; `icc_parity.py` 15/15 incl. 3 new Browser cases byte-identical to Python Playwright (archive_manifest.json runtime section included). Live Chrome tests: `crates/cyoa-engine/tests/browser_live.rs`. Pending: Sol review of browser.rs + mirror.rs browser paths + proxy `browser_route` (Codex back 2026-11-21); F-045 cookie import stays deferred.

**Session 8 (cont., 2026-11-20):** F-051 diagnostics (CLI + GUI), F-036 XLSX/XLS import, F-052 update checks + GUI Updates/Batch Check Center, and the GUI rebuilt to the Python layout and palette (titlebar, mode sidebar, Input section, badges, action rows, LOG; Download All flow). 321 Rust tests pass; fmt/clippy clean. GUI not compared visually with Python (screen capture denied) — owner check requested.

**Session 8 (2026-11-20):** Codex (Luna/Sol) unavailable until 2026-11-21; Claude worked alone, Ollama MiMo used for one port (rejected: did not compile) and one review (no findings). Done: GUI CYOA Manager library panel (F-038); F-037 V4 modernizer + CLI `--modernize-library` + ICC website hook (Python-identical fixture and CLI run); F-037 V5 viewer registration + GUI "Viewers…" panel (Python-identical fixture). 314 Rust tests pass; fmt and clippy (-D warnings) clean; e2e_parity 6/6, viewer_parity OK. Not run manually in the GUI. Pending: Sol review (V3 modules, library serve path, modernizer.rs, viewer_registry.rs), then F-028 browser/CDP.

**Session 7 (2026-11-19): F-037 offline viewers V1–V3 + consumers.** 306+ Rust tests pass; fmt and clippy (-D warnings) clean; `e2e_parity.sh` 6/6 OK; new `scripts/viewer_parity.sh` 5/5 identical to Python (manual viewer in embed/zip/both, auto selection + legacy index merge on a classic ICC page).
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
- Output is byte-identical to Python on the fixture site (`bash scripts/e2e_parity.sh`) and on a 300-image benchmark.
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

Resume procedure: `cargo test --offline`. Regenerate fixtures with `python fixtures/export_*.py`, run from the repo root. Pre-fetch any new crate with `cargo add` + `cargo fetch` before delegating (agents run `--offline`).

---
**Historical baseline (2026-11-18; superseded by the session 9 status above):** Phase 1 audit and Phase 2 architecture proposal complete.


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

## 2026-11-21 GUI parity pass (screenshot-verified against Python)
- Reports Center: own window 980x640, Python badges, filters, 12-per-page pager, CTk-blue Copy Failed.
- CYOA Manager Center (auto-add ON/OFF, library.sqlite3 Browse/Save, 5 library actions) now opens from the CYOA Mgr pill, as in Python.
- Updates: the main-row pill is gone; the panel opens from Settings > Maintenance > Updates and is styled as Python's Batch Check Center (900x620, pager). Deviation: the app-update check is a compact header button instead of Python's separate dialog.
- Pre-flight Check: own window, CTk default grey background.
- Unchecked checkboxes are rounded squares like CTk.
- Dropdowns use a CTkOptionMenu-style chevron segment; CYOA Manager Library restyled (own window, pager, CTk-blue buttons).
- Retry Assets/Images/Audio: both use native OS message boxes with the same titles and texts (verified in code).
- New `scripts/gui_e2e.sh`: drives the desktop app's real Add URL and Download All handlers (`CYOA_DESKTOP_E2E`) against the fixture site. Embed, Zip and Both outputs are byte-identical to the Python CLI; ZIP contents are identical (3 modes OK, 2026-11-21).
- `ICC_GUI=1 python scripts/icc_parity.py` runs the ICC/Pure cases through the desktop GUI handlers instead of the CLI. The Python side gets `--fonts`, because the GUI Fonts box defaults on in both apps. All 10 cases are OK, including the Browser strategy (2026-11-21).

## 2026-11-21 GUI parity (cont.) and security review
- `ICC_GUI=1 python scripts/cyoap_parity.py`: CYOAP Vue folder (15 files) and ICC folder (8 files) produced through the desktop GUI handlers are identical to Python (OK).
- Sol security review (task 20261121-150713-sol-02b261b997) of browser.rs, mirror.rs browser paths, proxy browser_route, ai_calls/ai_run, plugins, library serve, keyring. No traversal/zip-slip, command injection, disabled TLS or unsafe code found. Findings:
  1. HIGH, FIXED (owner approved 2026-11-21): proxy credentials via chromiumoxide `page.authenticate` are answered for *any* 401 challenge (verified in chromiumoxide 0.9.1 `handler/network.rs::on_fetch_auth_required`, no proxy/origin check), so a hostile origin can receive the proxy password. Proposed fix (needs owner approval, architecture): loopback relay proxy that adds `Proxy-Authorization` upstream, so Chrome never holds the credentials. Until then: avoid authenticated proxies with the Browser strategy.
  2. MEDIUM, FIXED: browser fallback / runtime capture bypass the native destination (SSRF) policy. Proposed fix: apply the destination check in the Fetch.requestPaused handler (fail private/loopback targets). Needs approval because it changes Python-parity behavior.
  3. MEDIUM, FIXED: `AiRuntime::collect_js` now retains at most 2000 files / 64 MiB (Python unbounded).
  4. MEDIUM, FIXED: settings exports recursively drop secret keys and strip URL userinfo and secret query parameters (`redact_export_value`; Python filters top-level names only).
- Gates: fmt clean, clippy -D warnings clean, 362 Rust tests pass.
- Fix for 1+2: new `cyoa_net::browser_relay` loopback relay. Chromium always runs behind it (`--proxy-server` + `--proxy-bypass-list=<-loopback>`); every request is checked against the client's `DestinationPolicy` (direct: resolved and pinned addresses), and an HTTP upstream proxy gets `Proxy-Authorization` from the relay, so Chromium never holds the proxy password (`page.authenticate` removed). HTTPS/SOCKS upstream proxies with credentials are refused for the browser. Without credentials they are passed to Chromium directly; residual risk: no local destination checks on that route.
- Sol re-review (task 20261121-162113-sol-5daa16bc4a): 8 findings, all addressed. Upstream routes now also resolve locally; one framed exchange per relayed HTTP connection (no pipelining, chunked or conflicting Content-Length rejected); absolute head, DNS and idle timeouts; handlers in a JoinSet, aborted with the relay; default proxy port 80; accept back-off; percent-decoded secret query names in exports; regular-file-only, read-capped JS collection.
- Real download test (cyoa.cafe/game/jrpg-traitor) found 2 parity bugs, now fixed: (a) `cafe.rs` candidate validation was an incomplete port, so the PocketBase target was rejected and the game fell back to a site mirror. It now matches Python `validate_candidate` and `_probe_project_endpoint`. (b) serde_json float parsing was off by 1 ulp (`float_roundtrip` feature now enabled in cyoa-parse); the 156 MB embed JSON is now byte-identical to Python. Also: chromiumoxide `WS Invalid message` warnings no longer flood the log (its target is filtered to errors).
- Real test, round 2, found 2 more bugs, now fixed: (c) ICC/Pure Website modes mirrored the cyoa.cafe catalogue page instead of the resolved viewer. Python `_v466_run_download` pre-resolves `cyoa.cafe/game/<key>`; ported as `run.rs::resolve_cafe_website_target`. (d) `integrity.rs` `JS_STATIC_REF_RE` used a fancy_regex backreference that ran for more than 5 minutes on a 400 KB bundle; it is now an equivalent linear `regex` with two quote alternatives (2.4 s on the same folder).
- Zip/Both duplicate images: Python keeps whichever duplicate finishes first (two Python runs differed by 220 entry names). Rust keeps the first in sorted path order, so its output is deterministic. The content set is identical.
- Final real test (2026-11-21, Python vs Rust CLI, cyoa.cafe jrpg-traitor and demon-s-blessing-expansion):
  - jrpg-traitor: Embed identical (156 MB, 0 failed). ICC ZIP/folder: 1060/1060 files identical except the timestamped backup_report. Zip/Both: same files and content, but different names kept for 389 duplicate images (Python keeps whichever thread finishes first). Pure folder: same count; Rust has 1 fewer failure.
  - demon-s-blessing-expansion: Embed JSON identical. The only failed asset (istockphoto HTTP 400) is the same on both sides; the Rust error text now matches requests `raise_for_status()`. ICC: `index.html` differs only in the order of Cloudflare `@font-face` blocks (served order varies). Pure folder: Python crashed (`OSError: Downloaded response body is empty`); Rust completed. Python's extra files are YouTube player internals (ads, heartbeat, icons) and duplicate copies. No remote references remain in any HTML/CSS/JS on either side.
  - Python leaves Selenium Manager's `se-metadata.json` and `chromedriver/` in the output folder; Rust does not.
  - Not identical: deep-scan failure wording ("HTTP 404" vs Python's "HTTP request failed").

## 2026-11-21 (evening): broad real-site and feature testing
- Sites (Python vs Rust): your-domme, FixingGodsFuckups, FalloutCYOA, newroad and hypnosisApp (CYOAP), outer-reincarnation, faproulette, scpyoa-v1-5 (audio), welcome-to-yorha (itch.io). Outputs are identical or differ only in documented ways (Google Fonts woff2 vs ttf by User-Agent, runtime `?time=` hashes, YouTube availability, report timestamps).
- Features verified: offline viewers (5 viewers byte-identical), modernize-library, viewer behaviour in Chromium (offline, internet blocked: same as Python), AI with Codex through an OpenAI-compatible shim (prompts identical after a newline fix), audio (14/14 YouTube tracks), FlareSolverr (test and download identical), itch.io (download identical; auth test moved to the current API), settings export (Rust redacts URL credentials, Python leaks them) and import (identical), self-test, dependency-check, verify.
- Fixed: mirror sends Python session headers; browser preference limited to the start origin (Python fails every CDN asset afterwards); mirror retries like urllib3 (3x on 429/5xx, Retry-After, effective URL); automatic recovery pass before finalize (deviation: Python has only the manual button), recovery never overwrites existing files and ignores PATH COLLISIONS records; offline_viewer ASSET regex made linear (minutes per bundle before); AI JS universal newlines; itch auth via api.itch.io/profile with a Bearer token.
- Sol reviews 20261121-223322 and a CLI bug check: all fixed except residual risks: FlareSolverr resolves remotely (same as Python). Fixed later the same evening: same-host different-port redirect hops connect through a client pinned to the vetted addresses (no DNS-rebinding gap); plugins exchange request/reply through per-call temp files instead of pipes, so descendants holding the handles cannot block anything.
- D8 APPROVED (owner, 2026-11-21): reqwest `native-tls` feature (Windows Schannel) for the shared HTTP client on Windows; other platforms keep rustls. Reason: faproulette.gg (Vercel) answered 429 to the rustls TLS fingerprint under crawl load while Python requests/curl got 200, which forced every page through the browser (32 min, 70 failures). With native TLS the crawl runs natively without browser fallback. New lockfile entries: native-tls, tokio-native-tls, hyper-tls (openssl crates only for non-Windows targets).
- Sol bug-check round 3 fixed: plugin reply bounded while the plugin runs; hop changes across retries capped at 10. Documented, not fixed: a transport error right after a redirect retries the previous hop URL (one fewer retry in a rare case).
