# Maintainer Guide

This guide is for contributors and maintainers. It documents repository structure, compatibility-sensitive behavior, documentation rules, testing gates, release checks, packaging rules, and review expectations.

If you are only using the program, read [Getting Started](./GETTING_STARTED.md) and [User Guide](./USER_GUIDE.md).

---

## 1. Design principles

- Stabilize existing behavior before adding new features.
- Prefer small, auditable patches over large rewrites.
- Treat remote content as untrusted.
- Keep optional dependencies optional.
- Preserve CLI compatibility.
- Preserve output compatibility.
- Keep GUI changes incremental.
- Keep Manual Inject and Offline Viewer workflows intact.
- Keep documentation compact but serious.
- Do not add placeholder files just to make the repository look larger.

---

## 2. Compact documentation rule

The repository should not accumulate many short Markdown files. Use this compact layout:

```text
README.md
CHANGELOG.md
AUDIT_REPORT.md
CONTRIBUTING.md
SECURITY.md
CREDITS.md
docs/GETTING_STARTED.md
docs/USER_GUIDE.md
docs/ADVANCED_FEATURES.md
docs/TROUBLESHOOTING.md
docs/MAINTAINER_GUIDE.md
```

Rules:

- Do not add `docs/README.md`; the root README is the entry point.
- Short topics should be merged into an existing guide.
- Advanced topics belong in `ADVANCED_FEATURES.md`.
- User workflow topics belong in `USER_GUIDE.md`.
- Failure/debug topics belong in `TROUBLESHOOTING.md`.
- Release/test/compatibility topics belong in `MAINTAINER_GUIDE.md`.
- Keep GitHub documentation English-only unless the project explicitly adopts multilingual docs.

---

## 3. Repository structure

Recommended public package structure:

```text
.
├── cyoa_downloader.py
├── README.md
├── CHANGELOG.md
├── AUDIT_REPORT.md
├── CONTRIBUTING.md
├── SECURITY.md
├── CREDITS.md
├── LICENSE
├── VERSION
├── requirements.txt
├── requirements-optional.txt
├── requirements-dev.txt
├── assets/
│   ├── logo-light.png
│   ├── logo-dark.png
│   └── logo-source.png
├── docs/
│   ├── GETTING_STARTED.md
│   ├── USER_GUIDE.md
│   ├── ADVANCED_FEATURES.md
│   ├── TROUBLESHOOTING.md
│   ├── GUI_QUEUE_GUIDE.md
│   └── MAINTAINER_GUIDE.md
├── examples/
├── tests/
└── .github/
```

Avoid including temporary reports, patch scripts, caches, or duplicated changelogs in release ZIPs.

---

## 4. Code areas

`cyoa_downloader.py` contains several areas that should be changed carefully:

- CLI argument parser;
- GUI and CustomTkinter/Tkinter fallback paths;
- theme, logo, and settings initialization;
- downloader engine;
- HTTP/session/retry logic;
- URL validation;
- path safety and atomic writes;
- archive extraction;
- project JSON extraction;
- embedded JavaScript parsing;
- asset scanner;
- image/audio/video/font downloader;
- offline viewer builder;
- Manual Inject;
- userscript serve helper;
- batch importer;
- dependency checker;
- self-test.

When changing one area, check whether it affects CLI, GUI, batch mode, and offline viewer output.

---

## 5. Compatibility-sensitive behavior

Do not break these without a release note and migration path:

| Area | Compatibility-sensitive behavior |
| --- | --- |
| CLI | Flag names, aliases, `--help`, `--dependency-check`, `--self-test`. |
| Batch | Column names and mode aliases. |
| Output | Folder names, ZIP layout, asset relative paths. |
| Offline viewer | `<name>_offline/`, `index.html`, Manual Inject, Auto-match. |
| Userscript helper | Serve-only helper paths and credits. |
| Settings | Import/export shape, secret redaction, default theme behavior. |
| Dependencies | Required vs optional boundaries. |
| Logging | Token/cookie/secret redaction. |
| GUI | Layout should be improved incrementally, not rewritten without need. |

---

## 6. Local development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Windows activation:

```powershell
.\.venv\Scripts\Activate.ps1
```

Run the GUI:

```bash
python cyoa_downloader.py --gui
```

Run a conservative test download:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --workers 2 --wait 120 --output debug_download
```

---

## 7. Required gates

Run before packaging or merging changes:

```bash
python -m py_compile cyoa_downloader.py
python -c "import ast, pathlib; ast.parse(pathlib.Path('cyoa_downloader.py').read_text(encoding='utf-8')); print('ast.parse OK')"
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test            # offline built-in checks
pytest -q
ruff check cyoa_downloader.py --select F821,F811,F601
```

Optional feature smoke test (after any change near the validator):

```bash
python cyoa_downloader.py --verify "some/finished/output_folder"
```

The self-test count is **37/37** as of the rev18–rev23 stabilization series (parity, cache
locking, after-destroy safety, validator, manifest round-trip, decode robustness, queue
policy). New behavior should add a self-test rather than relying on manual checks.

If `ruff` is unavailable, install development requirements:

```bash
pip install -r requirements-dev.txt
```

---

## 8. GUI checks

Manual GUI smoke test:

1. Launch `python cyoa_downloader.py --gui`.
2. Confirm the window opens.
3. Confirm version text shows the current value from `VERSION` (currently `1.0.5`).
4. Confirm original logo assets load.
5. Confirm action bar and feature tabs align.
6. Confirm the dark divider is visible but not bright white.
7. Switch theme between System, Dark, and Light.
8. Open Settings.
9. Open Offline Viewer Center.
10. Open Manual Inject.
11. Confirm logs do not freeze the UI.
12. Confirm dependency warnings are readable.

Headless GUI tests are useful but do not replace manual checks on Windows/macOS/Linux.

---

## 9. Documentation quality checklist

Each documentation change should answer:

- Who is this document for?
- What problem does it solve?
- What command should the user run?
- What should the expected result look like?
- What should the user do when the command fails?
- Does it duplicate another document?
- Are links relative and valid inside `docs/`?
- Does it preserve English-only GitHub docs?
- Is it specific enough to help beginners?

Avoid:

- one-paragraph placeholder docs;
- many tiny Markdown files;
- duplicate README files;
- unexplained command dumps;
- claims not supported by code behavior;
- obsolete flag names.

---

## 10. Security and safety checks

Review these when changing downloader behavior:

| Area | Required behavior |
| --- | --- |
| URL validation | Accept normal `http://` and `https://`; reject unsafe schemes for normal source download. |
| Path safety | Prevent path traversal and ZIP slip. |
| Archive extraction | Enforce file count/size/decompressed size limits. |
| Logging | Redact tokens, cookies, authorization headers, passwords, and secret-looking values. |
| Settings export | Redact or omit secrets. |
| Optional tools | Missing optional tools should warn, not crash normal workflows. |
| AI Assist | Avoid unlimited calls by default; support safe key handling. |

---

## 11. Release checklist

Before packaging:

- [ ] Program version is correct.
- [ ] `VERSION` file matches program version.
- [ ] README mentions the correct version.
- [ ] CHANGELOG has the release section.
- [ ] No duplicate README exists under `docs/`.
- [ ] Docs are English-only.
- [ ] Internal Markdown links are valid.
- [ ] Cache folders are not included.
- [ ] Original logo assets are present.
- [ ] Requirements files are current.
- [ ] Tests pass.
- [ ] GUI smoke screenshot is updated after visual changes.
- [ ] Examples are valid.
- [ ] Issue templates and PR template are useful, not empty placeholders.

---

## 12. Packaging policy

Public ZIP should include:

- source file;
- requirements;
- license and credits;
- compact docs;
- tests;
- examples;
- assets;
- GitHub templates and CI workflow.

Public ZIP should not include:

- `__pycache__/`;
- `.pytest_cache/`;
- `.ruff_cache/`;
- temporary build scripts;
- old patch reports;
- duplicated split changelogs;
- placeholder documentation;
- private keys or local settings;
- generated downloads.

---

## 13. Review policy

Before merging a patch, reviewers should check:

1. Does it preserve existing CLI flags?
2. Does it preserve output structure?
3. Does it keep optional dependencies optional?
4. Does it avoid unnecessary GUI rewrite?
5. Does it avoid breaking Manual Inject?
6. Does it update relevant docs?
7. Does it include tests or at least a clear manual test note?
8. Does it avoid adding many small docs?
9. Does it keep the version unchanged unless a release requires it?
10. Does it avoid storing or logging secrets?
