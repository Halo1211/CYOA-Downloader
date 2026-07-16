<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="170">
  </picture>
</p>

<h1 align="center">CYOA Downloader</h1>

<p align="center">
  Stable ICC/CYOA backup utility with GUI, CLI, batch import, offline viewer export, deep asset recovery, local preview tools, and GitHub-ready documentation.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg">
  <img alt="Release" src="https://img.shields.io/badge/Release-v1.0.5-orange.svg">
</p>

---

## What is this?

CYOA Downloader saves interactive web projects for offline inspection. It can
download a normal website, preserve an ICC-style project folder, recover media,
and verify the resulting backup.

## Start quickly

### Windows executable

Download the latest `CYOA-Downloader-Windows-x64.zip` from the
[GitHub Releases page](https://github.com/Halo1211/CYOA-Downloader/releases),
extract it, and run `CYOA Downloader.exe`.
The executable is unsigned, so Windows SmartScreen may require confirmation.

The executable includes Python and the packaged Python modules. It does not
bundle external programs that are large, licensed separately, or tied to the
user's machine. The diagnostic panel reports these separately:

- **FFmpeg** for media conversion and merging;
- **Deno** plus `yt-dlp-ejs` for current YouTube extraction;
- **Chrome/Chromium** and a driver for Selenium fallback;
- **Playwright Chromium** for browser automation;
- **unrar/7-Zip** for RAR extraction.

### Run from source

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py
```

For optional recovery and batch features:

```powershell
pip install -r requirements-optional.txt
python -m playwright install chromium
```

## Main commands

```bash
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py "https://example.com/cyoa" --icc-folder --output downloads
python cyoa_downloader.py --verify downloads
```

Running without arguments opens the GUI. The CLI supports batch files, ZIP or
folder output, retries, serving an offline viewer, and verification. See
[Getting Started](docs/GETTING_STARTED.md), [User Guide](docs/USER_GUIDE.md),
and [CLI reference](docs/CLI.md).

## Diagnostics and YouTube audio

The **Diagnostics** panel is important in both source and `.exe` modes. It
checks Python, importable packages, command-line tools, browser backends,
write permissions, settings, cache, and the runtime resources visible to a
frozen PyInstaller application.

For YouTube audio, a valid cookie file alone is not enough. The current
extractor also needs a current `yt-dlp`, the `yt-dlp-ejs` package, and a
supported JavaScript runtime such as Deno. If the panel reports a missing
runtime, install Deno or set `CYOA_YTDLP_DENO` to its full executable path.

Cookies must be exported as a fresh Netscape-format file when YouTube rotates
the browser session. Never commit cookies, settings, API keys, or downloaded
content to this repository.

## Build the Windows package

From a Windows development environment:

```powershell
.\tools\build_windows.ps1
```

The script installs the requirements, runs `CYOA-Downloader.spec`, and creates
`dist\CYOA-Downloader-Windows-x64.zip`. GitHub Actions performs the same build
for version tags and stores the ZIP as a workflow artifact.

The build creates a single executable and intentionally keeps FFmpeg, Deno, browsers, and RAR helpers as
diagnosed external dependencies. This keeps the package smaller and avoids
silently shipping machine-specific binaries.

## Repository map

| Path | Purpose |
| --- | --- |
| `cyoa_downloader.py` | CLI/GUI entry point |
| `cyoa_downloader_app/` | Application packages |
| `docs/` | User, troubleshooting, and maintainer guides |
| `examples/` | Safe sample batch inputs and templates |
| `tests/` | Offline regression tests |
| `tools/` | Local verification and Windows build helpers |
| `.github/` | CI, issue templates, and release automation |

The Windows executable uses `assets/cyoa_downloader.ico`, generated from the
transparent black `logo-light.png` mark in supported icon sizes.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q cyoa_downloader_app cyoa_downloader.py
pytest -q
ruff check cyoa_downloader_app tests tools --select F,E9,F63,F7,F82
```

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.
Security reports belong in [SECURITY.md](SECURITY.md). The project is released
under the license in [LICENSE](LICENSE).
