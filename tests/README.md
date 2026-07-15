# Tests

The test suite is for contributors and maintainers. Normal users do not need
to run it to download a CYOA.

Run all tests from the repository root:

```bash
python -m pytest -q
```

The suite is offline and deterministic where possible. A small number of GUI
tests are skipped unless a display is explicitly enabled. The current suite
also covers queue mode editing and CSV/TXT export/import.

Useful checks before opening a pull request:

```bash
python -m py_compile cyoa_downloader.py
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python -m pytest -q
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[Maintainer Guide](../docs/MAINTAINER_GUIDE.md) for the full release checklist.
