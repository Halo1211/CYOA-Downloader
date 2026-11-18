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
