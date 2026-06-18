# Contributing

Thank you for improving CYOA Downloader. This project is intentionally conservative: it handles user downloads, writes files to disk, parses untrusted remote content, and has both GUI and CLI users. Small, tested, backward-compatible changes are preferred over broad rewrites.

## Before opening a pull request

1. Create an issue or describe the problem clearly in the PR.
2. Confirm whether the change affects CLI flags, output layout, batch files, settings, Offline Viewer Center, Manual Inject, userscript helpers, or dependency behavior.
3. Keep version `1.0.1` unless the maintainer explicitly decides to cut a new release.
4. Avoid changing the main download behavior unless the bug is clearly understood.
5. Update documentation when user-facing behavior changes.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Required checks

Run these before opening a PR:

```bash
python -m py_compile cyoa_downloader.py
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
pytest -q
ruff check cyoa_downloader.py --select F821
```

If the change touches GUI code, also run the GUI manually when possible:

```bash
python cyoa_downloader.py --gui
```

Check the basic GUI workflow:

- paste URL;
- choose a mode;
- open settings;
- switch theme;
- open Offline Viewer Center;
- open Manual Inject;
- confirm logs do not freeze;
- confirm dark-mode divider is visible.

## Coding rules

- Prefer small helper functions over large rewrites.
- Do not remove existing CLI flags; add aliases when renaming is unavoidable.
- Do not change output formats casually.
- Do not make optional dependencies mandatory unless there is a documented reason.
- Do not install dependencies automatically without user consent.
- Keep log messages useful for beginners.
- Redact secrets before writing logs or reports.
- Treat URLs, archive members, filenames, and project JSON as untrusted input.
- Keep `--self-test` offline and deterministic.

## Documentation rules

The documentation is intentionally compact:

- Root `README.md` is the main entry point.
- `CHANGELOG.md` is the only changelog.
- `docs/` contains a small number of substantial guides.
- Do not add many tiny Markdown files for one-paragraph topics.
- If a new topic is short, merge it into an existing guide.
- Keep GitHub documentation in English.

## Pull request checklist

- [ ] The problem and fix are clearly described.
- [ ] Compatibility-sensitive behavior is preserved or documented.
- [ ] Tests pass locally.
- [ ] Documentation is updated when needed.
- [ ] New dependencies are justified and optional when possible.
- [ ] Screenshots are included for GUI visual changes.
