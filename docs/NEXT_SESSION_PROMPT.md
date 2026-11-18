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
1. KI-R1: payload spooling. Spill bodies over a threshold to temp files and stream embed output, to bound memory (Sol High). Measure peak working set against BENCHMARKS (98 MB at 300×50 KB).
2. GUI: "Import list" (reuse `cyoa_engine::batch`), per-item filename/mode, a manual download test against `rust/fixtures/e2e_site`, a 100k-row frame-time measurement, and history pruning.
3. Website/ICC mode (`--icc`, `--icc-folder`), then cyoap-vue, then pure website. Start by auditing `download/orchestrator.py` website branch and `_finalize_site_folder`.
4. Network options: proxy (F-016), DNS (F-017), VPN guard (F-018).
5. Fallbacks: gallery-dl, yt-dlp audio, browser/CDP, Cloudflare/FlareSolverr (D3), with a Sol review.
6. Output gaps: `cyoa_downloader.log`, `backup_report.txt` append, audio copy, offline viewer, cyoa-manager registration.
7. Release prep: release build, license notices, distribution dir, user docs.
