# Next session — resume prompt

Resume the CYOA Downloader Python→Rust migration on branch `migration/python-to-rust`. Do not repeat audits.

**Session 6 update:** latest commit `05d58da`; 219 tests pass; e2e is byte-exact. Done this session: proxy, DNS, VPN (engine, CLI and GUI), the report-summary parity, and the log file. See MIGRATION_PROGRESS.md at the top.

**Verified state (end of session 4, latest commit `298c571` or later):**
- From `rust/`: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --offline -- -D warnings` and `cargo test --workspace --offline` all pass (157 tests).
- From the repo root: `bash rust/scripts/e2e_parity.sh` prints 6 OK lines. Python and Rust `--both` outputs are byte-identical (timestamps excluded).
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
2. GUI: "Import list" (reuse `cyoa_engine::batch`), per-item filename/mode, a manual download test against `rust/fixtures/e2e_site`, a 100k-row frame-time measurement, and history pruning.
3. Website/ICC mode (s6: W4a-d done — manifest, project payload, integrity, second pass, finalize helpers; all Python-verified. Deep scan progress (s6): `website/deep_scan.rs` has `scan_file_for_assets` (1889-case corpus identical, linear on 2 MB) and the driver path helpers (299 cases identical). Deep scan DONE; ICC mode DONE (auto+classic; `rust/scripts/icc_parity.py` OK). GUI ICC mode selector DONE (s6). Smart strategy + route crawl DONE (icc_parity smart OK). NEXT (website): pure website modes (F-027, needs archive_profiler auto profile), browser strategy (CDP, D3). Earlier notes: `process_images(site_folder=)` reuse, orchestrator ICC branch (orchestrator.py 636-785) and CLI `--icc/--icc-folder`; keep CLI refusing ICC until deep scan parity holds). Earlier notes: W1+W2 done (paths, CSS, JS, byte-compatible BeautifulSoup HTML, _localize_html). Next W3: classic mirror = `_download_asset_impl` (website.py 1510-1560+) + entry fetch, implementing `website::AssetSink` over cyoa-net with containment-checked writes; then W4 ICC integration/_site.zip.
4. Network options: proxy done s6 (core + CLI --proxy/--proxy-mode/--proxy-http/--proxy-https/--proxy-bypass + saved-settings fallback via `cyoa_net::proxy::resolve_cli`). Remaining proxy gaps: GUI settings, persisting CLI flags to settings.json like Python, browser/FlareSolverr proxy. DNS (F-017) done for engine+CLI (DNS-1..3, Sol-reviewed). F-018 VPN guard done for engine+CLI (manual check needed: `cyoa-cli --vpn-policy require <url>` on a machine with/without VPN). GUI network panel done (s6: `cyoa-desktop/src/net.rs`, validated on enqueue, shared resolver/guard). s6: CLI network flags now persisted to settings.json (Python keys). GUI network panel also syncs with settings.json (loaded at start, saved on change). Remaining interop: other GUI settings (output dir, mode, workers) still only in the eframe state. Then output gaps (item 6) and fallbacks (item 5).

**Session 6 agent note:** `luna_implement` (isolated checkout) hung twice for 30 min (first: bridge `git status` 60 s timeout; second: stuck in `starting`, no codex pid). Prefer `luna_patch` next time. Root cause fixed in bridge.py (backup bridge.py.bak-20261118): bridge subprocesses inherited MCP stdin, which blocks CreateProcess on Windows; now stdin=DEVNULL. Needs MCP restart. Isolated mode also requires a clean main checkout (uncommitted AGENTS.md blocks it). `luna_patch` worked in ~1 min. After MCP restart + clean checkout, `luna_implement` isolated mode verified end-to-end (task 20261118-174357, patch applied cleanly). Commit before delegating isolated work.
5. Fallbacks: gallery-dl, yt-dlp audio, browser/CDP, Cloudflare/FlareSolverr (D3), with a Sol review.
6. Output gaps: (s6 done: `write_asset_failure_summary` = append to existing backup_report.txt / failed_assets.txt / remove broken_assets_report.html; .txt reports use Python text-mode CRLF on Windows; e2e script now byte-exact and fails on missing files) (s6 done: `cyoa_downloader.log` via `cyoa_engine::logfile` — Python format, INFO+, rotation 5 MiB×7, redaction identical on `fixtures/redact.json`), audio copy, offline viewer. (s6 done: CYOA Manager registration F-038 core + CLI --cyoa-manager; GUI toggle done; library browser panel pending.)
7. Release prep: s6 done — `rust/scripts/make_dist.sh` (release build + LICENSES bundle + notices + `docs/RUST_PREVIEW_GUIDE.md`), e2e on the dist exe byte-exact. Remaining: hand-add canonical texts for the 11 crates without license files, manual GUI smoke test of the packaged desktop exe, owner decision on publishing.

**Session 6 bridge state (owner action needed):** `active.json` recovery marker from task 20261118-180502-luna-077abfcc04 (DNS-3) blocked new Codex tasks. CLEARED by Claude on owner request (renamed to active.json.cleared-20261118); team_status shows no marker. MCP restart still needed to load the CRLF fix. Its work was reviewed and integrated in commit d020592 (the "outside scope" file was a CRLF-only false positive). Owner: delete `C:\Users\Albert\AI-Team-MiMo\task-logs\878f182801eafe60\active.json` after checking, then restart MCP. bridge.py now ignores EOL-only changes outside scope and exports only allowed paths (backup bridge.py.bak-20261118-crlf).
