# Test results (actual runs only)

| Date / time | Commit | Scope | Command | Result |
|---|---|---|---|---|
| 2026-11-18 00:20 | 0a312c7 | Python baseline | `python -m pytest -q -p no:cacheprovider` | 1633 passed, 21 skipped, exit 0 |
| 2026-11-18 00:47 | e5c1d13 | Rust Stage 0 | `scripts/gates.ps1` (fmt, check, clippy -D warnings, test) | all exit 0 |
| 2026-11-18 01:33 | ffd7866 | cyoa-core | fmt / clippy -D warnings / `cargo test -p cyoa-core` | exit 0; 7 tests (golden: 110 paths cases + 111 url cases; 4 junction/fs tests) |
| 2026-11-18 01:55 | (Stage 1b) | cyoa-storage | clippy -D warnings / `cargo test -p cyoa-storage` | exit 0; 3 tests (9 ZIP verdicts vs Python, JSON bytes vs Python, atomic write) |

Fixtures are regenerated from Python with `python fixtures/export_core.py` and `python fixtures/export_storage.py`, run from the repository root.

## Session 6 (2026-11-18)
- `cargo test --workspace --offline`: 219 passed, 0 failed. fmt check and clippy `-D warnings` are clean.
- `bash scripts/e2e_parity.sh`: 6 OK lines (byte-exact, timestamps ignored, missing files now fail).
- Differential fixtures against Python: `fixtures/dns_pure.json` (DNS wire/endpoint/validation, 5 tests), `fixtures/redact.json` (18 log-redaction cases), the report-summary strings in `reports.rs` tests, and the VPN name list (17 names, checked manually against Python).
- `cargo build --release --workspace --offline`: OK in 1m16s. `cyoa-downloader.exe` is 11.8 MB and `cyoa-desktop.exe` is 17.9 MB. The release CLI `--version`/`--help` work. Not done: a GUI smoke run of the release build and an e2e run against the release binary.
- Not tested: live Windows VPN interface enumeration (PowerShell run not permitted in this session), DoT against a real server, and DNS/proxy against real networks.

### Session 6 (cont.): settings interop and CYOA Manager

- `cargo test --workspace --offline`: 229 passed, 1 ignored (manual interop test), run twice. fmt and clippy clean. `e2e_parity.sh`: 6 OK.
- CLI network flags persisted to `settings.json` (`network_flags_are_persisted_to_settings_like_python`); invalid config writes nothing.
- GUI network panel round-trips Python `_SETTINGS_DEFAULTS` network keys exactly and keeps other keys.
- CYOA Manager interop: Python `add_to_cyoa_manager` / `add_archive_to_cyoa_manager` wrote a DB for 3 projects; `CYOA_MGR_INTEROP=<dir> cargo test -p cyoa-integrations -- --include-ignored` passed (Rust reports all 3 as duplicates; Rust-written rows equal Python's apart from id/date_added).
- Fixed a CLI test flake: tests shared one profile dir in parallel; each test now has its own.

### Session 6 (cont.): release preview

- `bash scripts/make_dist.sh` built `dist/cyoa-downloader-rust-0.1.0/` (36 MB: both exes, README from `docs/RUST_GUIDE.md`, `LICENSES/` for 295 crates, notices). Packaged CLI `--version`/`--help` OK.
- `RUST_BIN=<dist>/cyoa-downloader.exe bash scripts/e2e_parity.sh`: 6 OK (release binary byte-identical to Python).
- Not run: the packaged `cyoa-desktop.exe` (needs an interactive window check).

### Session 6 (cont.): website W4a-d

- `cargo test --workspace --offline`: 243 passed (+1 ignored manual interop); clippy, fmt clean; e2e 6 OK.
- New Python-generated fixtures: `backup_report.json`, `website_manifest.json`, `project_payload.json`, `integrity.json`, `integrity_report.json` (exporters in `fixtures/export_*.py`). All Rust outputs byte-identical, including CRLF.
- Rejected: a Luna draft for the same scope (self-reported incomplete, invented report format, no Python in its sandbox).
- CLI test flake fixed: the mock server stopped after a 3 s idle gap that a loaded parallel run could exceed (commit 5abbfd8 was made while that flake failed once; c85ffaf fixes it, suite then passed 3x under parallel clippy load).

### Session 6 (cont.): deep scan

- `cargo test --workspace --offline`: 245 passed, 2 ignored (manual interop, perf probe); clippy/fmt clean.
- `scan_file_for_assets`: 1889/1889 cases identical to Python (`fixtures/export_scan_file.py`). Perf probe: 2.1 MB inputs scan in <150 ms (release) after replacing fancy-regex look-behind/back-reference patterns that stalled for minutes.
- Deep-scan path helpers: 299/299 identical (`fixtures/export_deep_paths.py`).
- Local models (graded by compiling): mimo-heretic, deepseek-v4-pro-heretic and qwen2.5-coder:7b all failed the non-trivial Rust port task; none adopted.

## 2026-11-19 test-hygiene incident (keyring)

During a settings export/import differential run, a throwaway test profile contained fake secrets (`discord_bot_token`="SECRET123", `ai_api_key`="k"). Every Rust CLI run loads settings with the native keyring backend (DL-5 migration), so these fake values were written to the real Windows Credential Manager under service `CYOA Downloader` (Rust-only service; the Python app's `cyoa_downloader` / `cyoa_downloader_itch` entries were not touched). Any real value previously stored there by the Rust app would have been overwritten. Not read or deleted by the agent (secrets policy). Owner action: check Credential Manager > Windows Credentials > `CYOA Downloader`. Rule from now on: differential runs use settings without secret keys.

## 2026-11-21 (real sites, Python vs Rust CLI; commit dc0f967)

| Site / feature | Modes | Result |
|---|---|---|
| cyoa.cafe jrpg-traitor | embed, zip, both, icc, icc-folder, pure-folder | Embed byte-identical (156 MB); ICC 1060/1060 files; ZIP same content (dedup names deterministic in Rust) |
| cyoa.cafe demon-s-blessing-expansion | all | Embed identical; ICC differs only in Cloudflare @font-face order; Python Pure crashed, Rust completed |
| cyoa.cafe your-domme | icc-folder, pure-folder, FlareSolverr | 40/40 and 39/39 identical |
| cyoa.cafe scpyoa-v1-5 (audio) | embed, zip, both, icc-folder, pure-folder | ICC 461/461 identical incl. 14/14 YouTube tracks via yt-dlp |
| demxious FixingGodsFuckups, chuckeroo FalloutCYOA | embed, zip, icc-folder | Fallout byte-identical; Gods differs only in Google Fonts CSS |
| mushoku newroad, flccncn hypnosisApp (CYOAP) | cyoap-vue-folder, icc-folder | Same content; differences are Google Fonts slices and runtime `?time=` hashes |
| outer-reincarnation | pure-folder | Complete; Rust gets woff2 subsets |
| faproulette isekai-quest | pure-folder | After native TLS: 588/588 files, identical list, 0 browser fallbacks, 12 min (Python ~9 min) |
| itch.io welcome-to-yorha | --itch-only | 27/27 files; game ZIP and offline HTML5 identical |
| Offline viewers | ICC Plus LocalViewer, ICC Plus 2.10.17, ICC Remix, Viewer 1.8, Lt. Ouroumov | 5/5 byte-identical packages |
| Viewer playback (Chromium, internet blocked) | 8 ICC/Pure + 2 CYOAP outputs | Same behaviour as Python |
| Cheat panel | ICC Plus, ICC Remix | Panel, point types, set points, unlimited, select/deselect all, remove requirements: working, 0 JS errors, same as Python |
| AI Assist (Codex via OpenAI-compatible shim) | aggressive_recovery | 3 calls each, prompts identical |
| Other CLI features | self-test, dependency-check, verify, export/import settings, flaresolverr-test, itch-test | Pass; export safer than Python |

Gates on the final tree: `cargo fmt --all -- --check`, `cargo clippy --all-targets --offline -- -D warnings`, `cargo test --offline` (369 passed). GUI suites: `gui_e2e.sh`, `ICC_GUI=1 icc_parity.py` (10 OK), `ICC_GUI=1 cyoap_parity.py` (OK), `e2e_parity.sh`, `viewer_parity.sh`.

## 2026-11-22 (Windows release workflow)
- GitHub Actions run 44959993152 on tag `v1.1.6`: Rust release compilation and CLI verification succeeded. License packaging failed when `scripts/third_party_notices.py` ran `cargo metadata --offline` (exit 101); the captured exception omitted Cargo stderr. Artifact upload and release publication were skipped.
- Local follow-up edits replace offline metadata with locked metadata, retain full third-party/font license texts as a companion archive, and scope publishing to tag pushes. These edits have not yet run on GitHub. The existing tag still references the failed workflow revision.
