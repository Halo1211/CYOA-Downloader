# Maintainer Guide

This guide covers the repository layout, validation gates, diagnostics, and
the Windows release build. User-facing setup is in
[Getting Started](GETTING_STARTED.md).

## Repository layout

```text
cyoa_downloader.py          CLI/GUI entry point
cyoa_downloader_app/        application packages
assets/                     bundled logos and static resources
docs/                       user and maintainer documentation
examples/                   safe sample inputs
tests/                      offline regression tests
tools/                      local verification and build helpers
.github/                    CI, issue templates, release automation
```

Keep caches, downloaded projects, cookies, settings, API keys, generated ZIPs,
and PyInstaller output outside version control. Historical duplicate source is
not part of the supported application.

## Code boundaries

The entry point should remain small. Most behavior belongs in a package under
`cyoa_downloader_app/`:

- `gui/`: CustomTkinter/Tkinter UI, settings, and dialogs;
- `download/`: HTTP, assets, media, archives, and retries;
- `diagnostics/`: dependency, runtime, and self-test checks;
- `extraction/`: project JSON and embedded data parsing;
- `output/`: ICC, ZIP, viewer, and verification output;
- `network/`: sessions, throttling, proxy, and challenge helpers.

Preserve CLI flags, batch column names, output layouts, Manual Inject, Offline
Viewer, secret redaction, and the required/optional dependency boundary unless
the change includes a release note and migration guidance.

## Development setup

```bash
python -m venv .venv
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

On Windows, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Validation gates

Run these from the repository root before packaging or merging:

```bash
python -m compileall -q cyoa_downloader_app cyoa_downloader.py
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
pytest -q
ruff check cyoa_downloader_app tests tools --select F,E9,F63,F7,F82
```

The dependency check must distinguish Python imports from external capability
checks. In particular, `yt-dlp`, `yt-dlp-ejs`, a JavaScript runtime, FFmpeg,
browser backends, and RAR helpers are separate diagnostics. A frozen build
must also report its resource root instead of assuming the source checkout.

## Windows packaging

Build the supported single-file package with:

```powershell
.\tools\build_windows.ps1
```

`CYOA-Downloader.spec` collects the Python application, assets, yt-dlp/EJS,
CustomTkinter, Pillow, and the Playwright Python module. It intentionally does
not bundle FFmpeg, Deno, browsers, or RAR helpers. The output is:

```text
dist\CYOA Downloader.exe
dist\CYOA-Downloader-Windows-x64.zip
```

GitHub Actions runs this build for `v*` tags and publishes the ZIP as a
workflow artifact. A public release should attach that ZIP from the release
page rather than commit it to Git.

## GUI smoke test

After a Windows build, launch the executable and confirm:

1. The window opens and the current `VERSION` is shown.
2. Logo assets, Settings, Offline Viewer Center, and Manual Inject open.
3. Diagnostics show frozen mode and a valid bundled resource root.
4. Optional warnings are readable and do not prevent normal downloads.
5. A small ICC Folder download and `--verify` complete successfully.

## Security checklist

- Reject unsafe URL schemes and prevent path traversal/ZIP slip.
- Keep tokens, cookies, authorization headers, and secret settings out of logs.
- Treat remote HTML, JavaScript, archives, and media metadata as untrusted.
- Keep optional tools non-fatal for normal workflows.
- Never add browser cookie exports, `.env` files, downloaded content, or local
  settings to commits.
