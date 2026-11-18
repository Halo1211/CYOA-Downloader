# Test results (actual runs only)

| Date / time | Commit | Scope | Command | Result |
|---|---|---|---|---|
| 2026-11-18 00:20 | 0a312c7 | Python baseline | `python -m pytest -q -p no:cacheprovider` | 1633 passed, 21 skipped, exit 0 |
| 2026-11-18 00:47 | e5c1d13 | Rust Stage 0 | `scripts/gates.ps1` (fmt, check, clippy -D warnings, test) | all exit 0 |
| 2026-11-18 01:33 | ffd7866 | cyoa-core | fmt / clippy -D warnings / `cargo test -p cyoa-core` | exit 0; 7 tests (golden: 110 paths cases + 111 url cases; 4 junction/fs tests) |
| 2026-11-18 01:55 | (Stage 1b) | cyoa-storage | clippy -D warnings / `cargo test -p cyoa-storage` | exit 0; 3 tests (9 ZIP verdicts vs Python, JSON bytes vs Python, atomic write) |

Fixtures are regenerated from Python with `python rust/fixtures/export_core.py` and `python rust/fixtures/export_storage.py`, run from the repository root.

## Session 6 (2026-11-18)
- `cargo test --workspace --offline`: 219 passed, 0 failed. fmt check and clippy `-D warnings` are clean.
- `bash rust/scripts/e2e_parity.sh`: 6 OK lines (byte-exact, timestamps ignored, missing files now fail).
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

- `bash rust/scripts/make_dist.sh` built `rust/dist/cyoa-downloader-rust-0.1.0/` (36 MB: both exes, README from `docs/RUST_PREVIEW_GUIDE.md`, `LICENSES/` for 295 crates, notices). Packaged CLI `--version`/`--help` OK.
- `RUST_BIN=<dist>/cyoa-downloader.exe bash rust/scripts/e2e_parity.sh`: 6 OK (release binary byte-identical to Python).
- Not run: the packaged `cyoa-desktop.exe` (needs an interactive window check).
