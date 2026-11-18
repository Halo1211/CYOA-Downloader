# Next session — resume prompt

Resume the CYOA Downloader Python→Rust migration on branch `migration/python-to-rust`. Do not repeat audits.

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
3. Website/ICC mode: W1+W2 done (paths, CSS, JS, byte-compatible BeautifulSoup HTML, _localize_html). Next W3: classic mirror = `_download_asset_impl` (website.py 1510-1560+) + entry fetch, implementing `website::AssetSink` over cyoa-net with containment-checked writes; then W4 ICC integration/_site.zip.
4. Network options: proxy done s6 (core + CLI --proxy/--proxy-mode/--proxy-http/--proxy-https/--proxy-bypass + saved-settings fallback via `cyoa_net::proxy::resolve_cli`). Remaining proxy gaps: GUI settings, persisting CLI flags to settings.json like Python, browser/FlareSolverr proxy. DNS (F-017) done for engine+CLI (DNS-1..3, Sol-reviewed). F-018 VPN guard done for engine+CLI (manual check needed: `cyoa-cli --vpn-policy require <url>` on a machine with/without VPN). Next: GUI network settings (proxy+DNS+VPN) and settings persistence of CLI network flags.

**Session 6 agent note:** `luna_implement` (isolated checkout) hung twice for 30 min (first: bridge `git status` 60 s timeout; second: stuck in `starting`, no codex pid). Prefer `luna_patch` next time. Root cause fixed in bridge.py (backup bridge.py.bak-20261118): bridge subprocesses inherited MCP stdin, which blocks CreateProcess on Windows; now stdin=DEVNULL. Needs MCP restart. Isolated mode also requires a clean main checkout (uncommitted AGENTS.md blocks it). `luna_patch` worked in ~1 min. After MCP restart + clean checkout, `luna_implement` isolated mode verified end-to-end (task 20261118-174357, patch applied cleanly). Commit before delegating isolated work.
5. Fallbacks: gallery-dl, yt-dlp audio, browser/CDP, Cloudflare/FlareSolverr (D3), with a Sol review.
6. Output gaps: `cyoa_downloader.log`, `backup_report.txt` append, audio copy, offline viewer, cyoa-manager registration.
7. Release prep: release build, license notices, distribution dir, user docs.

**Session 6 bridge state (owner action needed):** `active.json` recovery marker from task 20261118-180502-luna-077abfcc04 (DNS-3) blocks new Codex tasks. Its work was reviewed and integrated in commit d020592 (the "outside scope" file was a CRLF-only false positive). Owner: delete `C:\Users\Albert\AI-Team-MiMo\task-logs\878f182801eafe60\active.json` after checking, then restart MCP. bridge.py now ignores EOL-only changes outside scope and exports only allowed paths (backup bridge.py.bak-20261118-crlf).
