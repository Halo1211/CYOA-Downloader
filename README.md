<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="170">
  </picture>
</p>

<h1 align="center">CYOA Downloader — v1.0 Release</h1>

<p align="center">
  Stable ICC/CYOA backup utility with GUI, CLI, batch import, offline viewer export, deep asset recovery, local preview tools, and GitHub-ready documentation.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg">
  <img alt="Release" src="https://img.shields.io/badge/Release-v1.0-orange.svg">
</p>

---

## What is this?

**CYOA Downloader** is a Python tool for creating local backups of Interactive CYOA / ICC projects. It can save a project as embedded JSON, ZIP, or a full offline ICC viewer package with HTML, CSS, JavaScript, project data, images, fonts, audio, video, and reports.

The project supports both a graphical desktop interface and a command-line interface for automation.

> **v1.0 terminology note:** user-facing **Website Mode** has been renamed to **ICC Mode**. Internal keys `website_zip` and `website_folder` are intentionally preserved so older batch files, settings files, and manifests keep working.

---

## Documentation map

| File | Purpose |
| --- | --- |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Full feature inventory and status matrix. |
| [`docs/CLI.md`](docs/CLI.md) | Complete CLI reference, examples, modes, network options, AI options, batch use. |
| [`docs/GUI.md`](docs/GUI.md) | GUI workflow, panels, queue usage, mode guide, troubleshooting from GUI. |
| [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) | Internal workflow: URL intake, project detection, asset scanning, download, reporting, ZIP/folder output. |
| [`START_HERE.md`](START_HERE.md) | Short beginner quick-start for first-time users. |
| [`INSTALLATION.md`](INSTALLATION.md) | Full beginner-friendly installation guide. |
| [`docs/QUICK_START.md`](docs/QUICK_START.md) | Short quick-start inside the docs folder. |
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | Documentation copy of the full installation guide. |
| [`docs/USAGE.md`](docs/USAGE.md) | Common workflows and ready-to-copy command examples. |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Missing assets, Cloudflare, slow downloads, GUI issues, EXE notes. |
| [`docs/EXE_BUILD.md`](docs/EXE_BUILD.md) | Recommended Windows EXE packaging workflow with PyInstaller. |
| [`docs/CREDITS.md`](docs/CREDITS.md) | Credits and third-party integration notes. |
| [`RELEASE_NOTES_v1.0.md`](RELEASE_NOTES_v1.0.md) | Release notes and migration guide. |

---

## Feature overview

### Interfaces

- **GUI mode** with queue, progress, logs, settings, language support, feature toggles, and local preview controls.
- **CLI mode** for automation, scripting, batch jobs, CI smoke tests, and server usage.
- **Batch import** from TXT, CSV, XLSX, XLS, remote CSV, or Google Sheets CSV export URL.

### Output modes

| Mode | CLI flag | Output | Best for |
| --- | --- | --- | --- |
| Embedded JSON | default | Single JSON with embedded image data | Portable project data, small/medium projects. |
| ZIP | `--zip` | ZIP with project JSON and external assets | Keeping assets separate but compressed. |
| Both | `--both` | Embedded JSON + ZIP | Archival backup when both formats are desired. |
| ICC ZIP | `--icc` | Offline ICC viewer ZIP | Shareable offline viewer package. |
| ICC Folder | `--icc-folder` | Offline ICC viewer folder | Inspection, preview, debugging, large downloads. |
| Pure Website ZIP | `--pure-website` | Site mirror ZIP without project JSON discovery first | Custom sites that do not expose a normal project JSON. |
| Pure Website Folder | `--pure-website-folder` | Site mirror folder | Custom-site debugging or manual inspection. |
| CYOAP Vue ZIP | `--cyoap-vue-website` | Dedicated `dist/platform.json` + `dist/nodes/list.json` ZIP | CYOAP Vue projects. |
| CYOAP Vue Folder | `--cyoap-vue-folder` | Dedicated CYOAP Vue folder | CYOAP Vue inspection and preview. |

### Asset coverage

- ICC project JSON discovery.
- Image fields such as `image`, `backgroundImage`, `rowBackgroundImage`, `objectBackgroundImage`, `defaultImage`, `thumbnail`, `coverImage`, `icon`, `portrait`, `selectedImage`, `unselectedImage`, and related ICC Plus fields.
- CSS/HTML/JS deep scan for linked assets.
- Image download for PNG, JPG/JPEG, GIF, WebP, BMP, SVG, AVIF, ICO.
- Audio detection for MP3, OGG, WAV, M4A, AAC, FLAC, OPUS, WEBA.
- Video detection for MP4, WebM, OGV, MKV, MOV, M4V.
- Font detection and localization for Google Fonts and direct WOFF/WOFF2/TTF/OTF/EOT files.
- Optional YouTube/SoundCloud audio recovery through `yt-dlp`.
- Optional gallery/post fallback through `gallery-dl`.

### Stability and safety

- Retry-capable HTTP session.
- URL scheme validation.
- Safe output path joining to reduce path traversal risk.
- Strict archive path validation.
- Atomic settings/cache writes.
- Rotating file logs.
- Sensitive value redaction in logs.
- Failed asset reports and batch failed URL reports.
- GUI log batching to avoid UI spam/freezes.
- Thread worker cap through `--threads`.
- 429 wait/backoff through `--wait-time`.
- Optional Cloudflare recovery using cloudscraper or FlareSolverr.
- Optional proxy, DNS, and HTTP/2 support.

---

## Install / Quick start

There are two normal ways to use this project:

| Method | Best for | Status |
| --- | --- | --- |
| **Windows EXE** | Non-technical Windows users | CYOA-Downloader-v1.0-Windows-x64.exe |
| **Python source install** | Current release, advanced users, maintainers | Fully supported now. |

For the shortest beginner path, open [`START_HERE.md`](START_HERE.md). For the full step-by-step guide, open [`INSTALLATION.md`](INSTALLATION.md).

### Option A — Windows EXE, when available

1. Open **GitHub → Releases**.
2. Download the Windows package, for example:

```text
CYOA-Downloader-v1.0-Windows-x64.zip
```

3. Extract the ZIP.
4. Run:

```text
CYOA-Downloader-v1.0-Windows-x64.exe
```

Do **not** run the EXE directly from inside the ZIP preview window. Extract it first.

### Option B — Windows source install, supported now

Open PowerShell inside the project folder and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If `py` is not available, replace `py` with `python`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If PowerShell blocks `.venv` activation, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Option C — Linux / macOS source install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

### Verify installation

Run these from the project folder:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

Expected behavior:

- dependency check prints installed/missing modules;
- self-test runs offline smoke checks;
- help text shows `--icc` and `--icc-folder`;
- old Website CLI flags are intentionally removed in v1.0 Release.

### Optional advanced feature setup

The default full install uses:

```bash
pip install -r requirements.txt
```

If installing manually, use this map:

| Feature | Packages / tools |
| --- | --- |
| Core downloader | `requests`, `urllib3`, `beautifulsoup4` |
| GUI | `customtkinter`, `Pillow`, Tkinter from Python/OS |
| Batch Excel import | `pandas`, `openpyxl`, `xlrd` |
| Deep scan helpers | `json5`, `tldextract`, `httpx[h2]`, `dnspython` |
| Cloudflare recovery | `cloudscraper`, optional external FlareSolverr service |
| Browser fallback | `selenium` and/or `playwright`; for Playwright also run `python -m playwright install chromium` |
| Media extraction | `yt-dlp`, `gallery-dl` |
| AI key storage | `keyring`; local AI can use Ollama |

Full installation details are in [`INSTALLATION.md`](INSTALLATION.md).

## Run the GUI

```bash
python cyoa_downloader.py
```

Run with `--gui` to force GUI even when arguments are present:

```bash
python cyoa_downloader.py --gui
```

The GUI loads the bundled light/dark logo from `assets/logo-light.png` and `assets/logo-dark.png`. If the files are missing, the script falls back to embedded logo data and then a text fallback.

---

## Run the CLI

```bash
python cyoa_downloader.py "https://example.com/cyoa" -o output
python cyoa_downloader.py --zip "https://example.com/cyoa" -o output
python cyoa_downloader.py --both "https://example.com/cyoa" -o output
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder
```

Preview a downloaded ICC folder through localhost:

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --serve
```

Run diagnostics/smoke checks:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --userscript-info
```

Full CLI documentation is in [`docs/CLI.md`](docs/CLI.md).

---

## ICC Mode and legacy flag migration

Use these v1.0 flags:

```bash
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

The old user-facing flags were intentionally removed for consistency:

| Removed | Replacement |
| --- | --- |
| `--website` | `--icc` |
| `-W` | `--icc` |
| `--website-folder` | `--icc-folder` |

Internal compatibility is still preserved:

| Internal key | Status | Reason |
| --- | --- | --- |
| `website_zip` | Supported | Existing batch/settings/manifest compatibility. |
| `website_folder` | Supported | Existing batch/settings/manifest compatibility. |

New batch files may use `icc`, `icc_zip`, or `icc_folder`; older files using `website_zip` and `website_folder` still work.

---

## Batch import

TXT format:

```text
https://example.com/cyoa/
https://example.com/cyoa2/ | MyFilename
https://example.com/cyoa3/ | MyFilename | icc
https://example.com/cyoa4/ | MyFolder | icc_folder
```

CSV/XLSX columns are case-insensitive:

| Column | Required | Notes |
| --- | --- | --- |
| `url`, `link`, `urls`, `links` | Yes | Full URL beginning with `http://` or `https://`. |
| `filename`, `name`, `output`, `title`, `file` | No | Output filename/folder name. |
| `mode`, `output_mode`, `type` | No | `embed`, `zip`, `both`, `icc`, `icc_zip`, `icc_folder`, `website_zip`, `website_folder`, `pure_website_zip`, `pure_website_folder`, `cyoap_vue_zip`, `cyoap_vue_folder`. |

Run a batch:

```bash
python cyoa_downloader.py --list batch.csv -o outputs
```

---

## Reports and logs

Common output files:

| File | Purpose |
| --- | --- |
| `cyoa_downloader.log` | Rotating runtime log for troubleshooting. |
| `backup_report.txt` | Summary of downloaded and failed assets for ICC/website outputs. |
| `failed_assets.txt` | Plain-text failed asset report when no backup report is available. |
| `failed_urls.txt` | Batch-level URL failures. |
| `project.json` | Extracted project data when available. |

Sensitive log-looking values such as tokens, passwords, cookies, and bearer authorization strings are redacted before logging.

---

## Local Serve preview and helper policy

The local server is intended for downloaded/offline output only. It may expose a small localhost Tools overlay for debugging, accessibility checks, storage export/import, cache clearing, and quality-of-life testing.

Bundled userscript helper credit:

- Name: **IntCyoaEnhancer**
- Author: **agreg**
- License: **MIT**
- Source: **GreasyFork script 438947**

The bundled helper is a localhost/offline integration route. This project does not claim ownership of the original IntCyoaEnhancer project.

---

## Build Windows EXE

Recommended release form is a ZIP containing an onedir PyInstaller build:

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name "CYOA Downloader" --add-data "assets;assets" cyoa_downloader.py
```

See [`docs/EXE_BUILD.md`](docs/EXE_BUILD.md) for icons, onedir vs onefile, and GitHub release asset recommendations.

---

## Disclaimer

Use this tool only for content you are allowed to archive. Local Serve tools, debugging helpers, userscript integration, Cloudflare recovery, and optional gallery/media backends must be used responsibly and only where permitted. The project is designed for offline backup, accessibility, debugging, and quality-of-life testing of downloaded CYOA output.

---

## Credits

See [`docs/CREDITS.md`](docs/CREDITS.md) and [`CREDITS.md`](CREDITS.md).

---

## License

This project is released under the **MIT License**. See [`LICENSE`](LICENSE).

---

## Contributing

Backward-compatible improvements are welcome. Please preserve old internal mode keys, avoid breaking batch/settings/manifest compatibility, and include smoke tests for CLI parsing, path safety, batch import, and ICC aliases.
