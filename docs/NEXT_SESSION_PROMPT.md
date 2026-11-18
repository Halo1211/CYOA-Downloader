# Next session — resume prompt

Resume the CYOA Downloader Python→Rust migration on branch `migration/python-to-rust`. Do not repeat audits.

**State (2026-11-18, session 4):**
- Download engine and CLI MVP work end to end, at byte parity with Python on the fixture site.
- Verify: `bash rust/scripts/e2e_parity.sh` from the repo root (expects six OK lines).
- Gates from `rust/`: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --offline -- -D warnings`, `cargo test --workspace --offline` (141 passing at commit 7b4257d or later).
- See `git log` for the latest verified commit.

**Engine modules (`rust/crates/cyoa-engine/src`):**
- `fetch`: per-asset fetch.
- `pipeline`: `process_images`.
- `discover_net`: static `get_project_source`.
- `output`: naming, ZIP.
- `reports`: metadata, failed_assets.
- `run`: shared backend for CLI and GUI. Use `run::run_download(opts, cancel, progress_tx)`.
- `cyoa-cli`: clap MVP.

**Agent notes:**
- Luna's `luna_implement` write access is intermittent ("apply_patch failed", "Access is denied"). Long tasks time out and leave partial or garbled files (PowerShell backtick-n escapes, literal `\r\n`, stray `.stage-*` files in the repo root).
- Never trust agent-reported gate results; rerun them.
- If Luna's writes fail, Claude implements directly. Pre-fetch crates (`cargo add` + `cargo fetch`) before delegating; agents build `--offline`.

**Next actionable tasks, in order:**
1. Sol security review of `run.rs`, `output.rs`, `reports.rs` and `cyoa-cli` (ZIP writer, output paths, metadata write overwrite).
2. GUI Batch A–C in `cyoa-desktop` (egui/eframe, decision D1). Run `run_download` on a Tokio runtime thread and pass progress over the bounded channel, with a cancel button.
3. Remaining parity (see the OVERNIGHT_REPORT "Not yet ported" list and KNOWN_ISSUES KI-R1..R4):
   - transport retries
   - payload spooling (memory)
   - `-L` list mode
   - output log file
   - audio copy
   - website/ICC modes
   - proxy/DNS
4. Benchmarks (BENCHMARKS.md), then a release build.
