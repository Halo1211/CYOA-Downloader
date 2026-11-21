# Contributing

Thank you for improving CYOA Downloader. Rust is the primary application, with
the original Python implementation retained under `old/` as the runnable
reference and parity source.

## Rust changes

Run from the repository root. Keep behavior compatible, add or update parity
fixtures where needed, and document intentional differences in
`docs/FEATURE_PARITY.md`.

```powershell
cargo fmt --all -- --check
cargo check --all-targets
cargo clippy --all-targets -- -D warnings
cargo test
```

Parity utilities live in `scripts/` and Python fixture exporters live in
`fixtures/`. Python reference imports come from `old/`.

## Python reference changes

Install Python dependencies from the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r old/requirements.txt
python -m pip install -r old/requirements-dev.txt
```

Run the Python checks from the repository root:

```powershell
python -m compileall -q old/cyoa_downloader_app old/cyoa_downloader.py
python old/tools/check_repository.py --root .
python old/cyoa_downloader.py --help
python old/cyoa_downloader.py --dependency-check
python old/cyoa_downloader.py --self-test
python -m pytest -q old/tests
python -m ruff check old
```

For GUI or browser changes, run the relevant fixtures on a machine with a
desktop and installed browser runtime. The default tests use local fixtures and
skip unavailable live integrations.

## Documentation

Keep GitHub documentation in English. Update `README.md` for user-facing
changes, `docs/FEATURE_PARITY.md` for parity differences, and
`docs/MIGRATION_PROGRESS.md` when migration status changes.