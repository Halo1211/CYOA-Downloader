# Next session — resume prompt

Resume the CYOA Downloader Python→Rust migration on branch `migration/python-to-rust`. Do not repeat audits.

**Session 9 update 2 (2026-11-22):** All features ported (F-045 browser cookies, yt-dlp browser-cookie retry, route continuation, bundled viewers). Next: owner merges the PR; optional release build smoke test.

**Session 9 update (2026-11-21, commit dc0f967 + docs):** Security relay for the browser, four Sol review rounds (all fixed, residuals in SECURITY_REVIEW.md), real-site testing of 12 sites and all major features (TEST_RESULTS.md), native TLS on Windows (DL-10), urllib3-style mirror retries, automatic recovery pass. 369 tests pass. Not pushed (owner pushes). Next: (1) owner review of the branch / PR to main; (2) optional: reduce deep-scan "not found" probes to Python's set; (3) release build smoke test (`scripts/make_dist.sh`).

**Session 8 update 3 (2026-11-20):** F-043 AI (A1–A4) and F-044 plugins done. Sol reviews pending (usage limit until 2026-11-21 11:19): browser.rs + mirror browser paths + proxy browser_route; ai_calls.rs/ai_run.rs/ai_panel.rs (secrets, endpoint client); plugins.rs (process spawning). Remaining features: F-045 browser cookie import (deferred, D3), F-049 GUI settings panels, F-055 packaging, manual GUI smoke tests.

**Session 8 update 2 (2026-11-20):** F-028 browser/CDP done and Python-identical on `icc_parity.py` (15/15, Browser cases need Python Playwright + Chrome/Edge). Next: (1) after 2026-11-21 `sol_review` of `cyoa-engine/src/browser.rs`, the browser paths in `website/mirror.rs` (`fetch`, `fetch_with_browser`, `capture_runtime`), `fetch.rs` `headless_image`, and `cyoa-net/src/proxy.rs` `browser_route` (plus the earlier pending list below); (2) manual GUI smoke test with archive strategy Browser; (3) remaining gaps: F-045 browser cookie import (deferred), F-043/F-044 AI + plugins, F-049 settings panels.

**Session 8 update (2026-11-20):** GUI library panel, F-037 V4 (modernizer, CLI, ICC hook) and V5 (registration + GUI panel) done and Python-identical. Next: (1) after 2026-11-21 `sol_review` of itch_html5.rs, manager_serve.rs, preserved_index.rs, run.rs viewer stage, desktop library.rs, modernizer.rs, viewer_registry.rs; (2) F-028 browser/CDP; (3) manual GUI smoke test (Library…, Viewers…).

**Session 7 update (2026-11-19):** F-037 V1–V3 done and Python-identical (see MIGRATION_PROGRESS top). Next, in order: (1) after 2026-11-21, `sol_review` of itch_html5.rs, manager_serve.rs, preserved_index.rs and the run.rs viewer stage; (2) ask the owner to approve F-037 V4 (modernizer: `analyze_site`, `modernize_site`, `modernize_collection`, CLI `--modernize-library` / `--viewer-collection` / `--modernize-output`, settings `offline_viewer_website_enabled` hook in ICC mode) and V5 (GUI viewer panel + viewer registration); (3) GUI library panel (uses `manager_serve`); (4) F-028 browser/CDP.

**Session 6 update:** latest commit `05d58da`; 219 tests pass; e2e is byte-exact. Done this session: proxy, DNS, VPN (engine, CLI and GUI), the report-summary parity, and the log file. See MIGRATION_PROGRESS.md at the top.

**Verified state (end of session 4, latest commit `298c571` or later):**
- From the repository root: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --offline -- -D warnings` and `cargo test --workspace --offline` all pass (157 tests).
- From the repo root: `bash scripts/e2e_parity.sh` prints 6 OK lines. Python and Rust `--both` outputs are byte-identical (timestamps excluded).
- Engine: discovery (static) → fetch (cache, retries, 0.3 s domain interval) → pipeline → output/reports. Shared backend: `cyoa_engine::run::run_download`.
- CLI: url, filename, -u, -o, -L (txt/csv), -z, -b, -t, -w.
- GUI (egui, `cyoa-desktop`): single-URL queue, background worker, cancellation, persisted settings.
- Benchmarks: `docs/BENCHMARKS.md`. Security reviews: `docs/SECURITY_REVIEW.md` (session 4 sections).

**Agent notes:**
- Luna and Sol could not write files for most of session 4 ("Failed to write file", "apply_patch failed"). Use `sol_review` / `luna_review` for reviews or code proposals, and let Claude apply edits.
- Never trust agent-reported gate results.
- Pre-fetch crates before delegating.
- Python heredocs mangle `
 
 	` escapes in Rust source: use the Write/Edit tools or raw strings.
- Tests and parity scripts must set `USERPROFILE`/`HOME` to a temp dir, or they will touch the user's real image cache.

**Next actionable tasks, in order:**
1. (done s5) KI-R1 spooling; GUI list import; 100k-row frame time; website W1 path mapping.
2. GUI: "Import list" (reuse `cyoa_engine::batch`), per-item filename/mode, a manual download test against `fixtures/e2e_site`, a 100k-row frame-time measurement, and history pruning.
3. Website/ICC mode (s6: W4a-d done — manifest, project payload, integrity, second pass, finalize helpers; all Python-verified. Deep scan progress (s6): `website/deep_scan.rs` has `scan_file_for_assets` (1889-case corpus identical, linear on 2 MB) and the driver path helpers (299 cases identical). Deep scan DONE; ICC mode DONE (auto+classic; `scripts/icc_parity.py` OK). GUI ICC mode selector DONE (s6). Smart strategy + route crawl DONE (icc_parity smart OK). Pure website DONE (icc_parity 7/7 OK incl. pure auto/classic/smart). s6 later: CYOA.CAFE resolver + static records, CYOAP Vue, custom-viewer fallback DONE (cyoap_parity OK, icc_parity 7/7). NEXT: GUI cyoap modes, browser strategy (CDP, D3), fallbacks (gallery-dl/yt-dlp), offline viewers (F-037), fonts (F-030). Earlier notes: `process_images(site_folder=)` reuse, orchestrator ICC branch (orchestrator.py 636-785) and CLI `--icc/--icc-folder`; keep CLI refusing ICC until deep scan parity holds). Earlier notes: W1+W2 done (paths, CSS, JS, byte-compatible BeautifulSoup HTML, _localize_html). Next W3: classic mirror = `_download_asset_impl` (website.py 1510-1560+) + entry fetch, implementing `website::AssetSink` over cyoa-net with containment-checked writes; then W4 ICC integration/_site.zip.
4. Network options: proxy done s6 (core + CLI --proxy/--proxy-mode/--proxy-http/--proxy-https/--proxy-bypass + saved-settings fallback via `cyoa_net::proxy::resolve_cli`). Remaining proxy gaps: GUI settings, persisting CLI flags to settings.json like Python, browser/FlareSolverr proxy. DNS (F-017) done for engine+CLI (DNS-1..3, Sol-reviewed). F-018 VPN guard done for engine+CLI (manual check needed: `cyoa-cli --vpn-policy require <url>` on a machine with/without VPN). GUI network panel done (s6: `cyoa-desktop/src/net.rs`, validated on enqueue, shared resolver/guard). s6: CLI network flags now persisted to settings.json (Python keys). GUI network panel also syncs with settings.json (loaded at start, saved on change). Remaining interop: other GUI settings (output dir, mode, workers) still only in the eframe state. Then output gaps (item 6) and fallbacks (item 5).

**Session 6 agent note:** `luna_implement` (isolated checkout) hung twice for 30 min (first: bridge `git status` 60 s timeout; second: stuck in `starting`, no codex pid). Prefer `luna_patch` next time. Root cause fixed in bridge.py (backup bridge.py.bak-20261118): bridge subprocesses inherited MCP stdin, which blocks CreateProcess on Windows; now stdin=DEVNULL. Needs MCP restart. Isolated mode also requires a clean main checkout (uncommitted AGENTS.md blocks it). `luna_patch` worked in ~1 min. After MCP restart + clean checkout, `luna_implement` isolated mode verified end-to-end (task 20261118-174357, patch applied cleanly). Commit before delegating isolated work.
5. Fallbacks: gallery-dl, yt-dlp audio, browser/CDP, Cloudflare/FlareSolverr (D3), with a Sol review.
6. Output gaps: (s6 done: `write_asset_failure_summary` = append to existing backup_report.txt / failed_assets.txt / remove broken_assets_report.html; .txt reports use Python text-mode CRLF on Windows; e2e script now byte-exact and fails on missing files) (s6 done: `cyoa_downloader.log` via `cyoa_engine::logfile` — Python format, INFO+, rotation 5 MiB×7, redaction identical on `fixtures/redact.json`), (s7 done: embed/both audio merge, offline viewer V1–V3). (s6 done: CYOA Manager registration F-038 core + CLI --cyoa-manager; GUI toggle done; library browser panel pending.)
7. Release prep: s6 done — `scripts/make_dist.sh` (release build + LICENSES bundle + notices + `docs/RUST_GUIDE.md`), e2e on the dist exe byte-exact. Remaining: hand-add canonical texts for the 11 crates without license files, manual GUI smoke test of the packaged desktop exe, owner decision on publishing.

**Session 6 bridge state (owner action needed):** `active.json` recovery marker from task 20261118-180502-luna-077abfcc04 (DNS-3) blocked new Codex tasks. CLEARED by Claude on owner request (renamed to active.json.cleared-20261118); team_status shows no marker. MCP restart still needed to load the CRLF fix. Its work was reviewed and integrated in commit d020592 (the "outside scope" file was a CRLF-only false positive). Owner: remove the matching `active.json` recovery marker from the configured task-logs directory after checking, then restart MCP. bridge.py now ignores EOL-only changes outside scope and exports only allowed paths (backup bridge.py.bak-20261118-crlf).
