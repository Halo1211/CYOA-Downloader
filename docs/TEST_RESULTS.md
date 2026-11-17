# Test results (actual runs only)

| Date / time | Commit | Scope | Command | Result |
|---|---|---|---|---|
| 2026-11-18 00:20 | 0a312c7 | Python baseline | `python -m pytest -q -p no:cacheprovider` | 1633 passed, 21 skipped, exit 0 |
| 2026-11-18 00:47 | e5c1d13 | Rust Stage 0 | `scripts/gates.ps1` (fmt, check, clippy -D warnings, test) | all exit 0 |
| 2026-11-18 01:33 | ffd7866 | cyoa-core | fmt / clippy -D warnings / `cargo test -p cyoa-core` | exit 0; 7 tests (golden: 110 paths cases + 111 url cases; 4 junction/fs tests) |
| 2026-11-18 01:55 | (Stage 1b) | cyoa-storage | clippy -D warnings / `cargo test -p cyoa-storage` | exit 0; 3 tests (9 ZIP verdicts vs Python, JSON bytes vs Python, atomic write) |

Fixtures are regenerated from Python with `python rust/fixtures/export_core.py` and `python rust/fixtures/export_storage.py`, run from the repository root.
