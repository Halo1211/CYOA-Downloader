<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="170">
  </picture>
</p>

<h1 align="center">CYOA Downloader v1.0.1</h1>

<p align="center">
  A stable GUI and CLI utility for backing up Interactive CYOA / ICC projects, linked assets, media references, fonts, website resources, and offline viewer builds.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg">
  <img alt="Release v1.0.1" src="https://img.shields.io/badge/Release-v1.0.1-orange.svg">
  <img alt="GUI and CLI" src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-purple.svg">
</p>

---

## What is this?

**CYOA Downloader** creates local backups of Interactive CYOA / ICC projects. It can download the project data, scan project JSON and viewer files for linked assets, localize images/fonts/media where possible, and build ZIP or folder outputs that are easier to archive, inspect, preview, and recover.

The project is intentionally usable in two ways:

- **GUI mode** for normal users who want buttons, progress logs, retry tools, local preview, settings, and offline viewer utilities.
- **CLI mode** for advanced users, automation, batch jobs, scripted backups, and testing.

Version **1.0.1** keeps the 1.0 input/output model and compatibility expectations intact. The stabilization work focuses on safer dependency reporting, clearer FFMPEG guidance, dark-mode visual consistency, System theme default, documentation cleanup, and additional validation gates. The program version remains exactly `1.0.1`.

---

## Main capabilities

### Download and archive modes

| Mode | CLI flag | Output | Best use case |
| --- | --- | --- | --- |
| Embedded JSON | default | Single JSON file with embedded data where possible | Small projects or portable project snapshots. |
| ZIP | `--zip` | ZIP with project JSON and external assets | Archival backups with separated files. |
| Both | `--both` | Embedded JSON + ZIP | Conservative backups when you want both formats. |
| ICC ZIP | `--icc` | Full offline ICC viewer ZIP | Shareable offline viewer package. |
| ICC Folder | `--icc-folder` | Full offline ICC viewer folder | Debugging, inspection, large outputs, local preview. |
| Pure Website ZIP | `--pure-website` | Site mirror ZIP without normal project detection first | Custom viewers or non-standard sites. |
| Pure Website Folder | `--pure-website-folder` | Site mirror folder | Manual inspection of custom viewers. |
| CYOAP Vue ZIP | `--cyoap-vue-website` | Dedicated CYOAP Vue ZIP | Projects using `dist/platform.json` and `dist/nodes/list.json`. |
| CYOAP Vue Folder | `--cyoap-vue-folder` | Dedicated CYOAP Vue folder | CYOAP Vue inspection and preview. |

### Asset coverage

The downloader handles the common CYOA/ICC asset patterns found across classic ICC, ICC Plus, and custom viewers:

- project JSON discovery from direct JSON, embedded app bundles, viewer config, and archive payloads;
- image fields such as `image`, `backgroundImage`, `rowBackgroundImage`, `objectBackgroundImage`, `defaultImage`, `thumbnail`, `coverImage`, `headerImage`, `icon`, `portrait`, `avatar`, `selectedImage`, `unselectedImage`, and ICC Plus border/loading/backpack keys;
- CSS, HTML, and JavaScript deep scans for linked files;
- Google Fonts and direct font files (`woff`, `woff2`, `ttf`, `otf`, `eot`);
- audio fields, direct BGM URLs, SoundCloud URLs, and YouTube IDs where supported by optional tools;
- common image, audio, video, script, style, and text asset extensions;
- failed asset reporting and retry workflows.

### Safety and stability features

- URL scheme guard for HTTP/HTTPS sources.
- Safer output path joining to reduce path traversal risk.
- Strict archive member validation to reduce ZIP slip risk.
- Archive extraction limits to reduce decompression abuse risk.
- Atomic settings/cache writes.
- Rotating log files.
- Token, cookie, password, bearer, and API-key redaction in logs.
- Non-blocking GUI log queue.
- Thread cap for parallel downloads.
- Clear dependency check output.
- Non-fatal FFMPEG warning unless a feature truly requires FFMPEG.
- `--self-test` for offline smoke validation.

---

## Repository layout

| Guide | Content |
| --- | --- |
| [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) | Installation, first run, first GUI backup, first CLI backup, FFMPEG and yt-dlp setup. |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | GUI panels, CLI workflows, batch files, output modes, Offline Viewer Center, Manual Inject. |
| [`docs/ADVANCED_FEATURES.md`](docs/ADVANCED_FEATURES.md) | AI Assist, Cloudflare handling, proxies, DNS, HTTP/2, media recovery, theme/logo behavior, serve tools. |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Practical fixes for dependency errors, failed URLs, missing assets, GUI issues, FFMPEG/yt-dlp, and batch problems. |
| [`docs/MAINTAINER_GUIDE.md`](docs/MAINTAINER_GUIDE.md) | Project structure, tests, release gates, documentation rules, and compatibility requirements. |

There is no second `docs/README.md`. The root README is the single entry point.

---

## Quick start

### 1. Install Python

Use **Python 3.10 or newer**. On Windows, install Python from python.org or the Microsoft Store and enable “Add Python to PATH” if the installer offers it.

### 2. Create a virtual environment

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Verify dependencies

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

A clean setup should show dependency status, pass the offline self-test, and print the CLI help.

### 4. Start the GUI

```bash
python cyoa_downloader.py
```

Running the script without arguments opens the GUI. You can also force it with:

```bash
python cyoa_downloader.py --gui
```

---

## First GUI backup

1. Open the app with `python cyoa_downloader.py`.
2. Paste the CYOA or viewer URL.
3. Choose the output mode. For most beginners, start with **ICC Folder** or **ICC ZIP** if available in the GUI.
4. Click **Download All**.
5. Wait for the log to finish.
6. Open the output folder.
7. Read `backup_report.txt` or `failed_assets.txt` if some assets fail.
8. Use **Retry Assets**, **Retry Images**, or **Retry Audio** if the GUI reports recoverable failures.

For offline viewer use, open **Offline Viewer Center** and use **Auto-match** first. If auto-match cannot identify the correct viewer, use **Inject** and select a project source manually.

---

## Useful CLI examples

Download the default output:

```bash
python cyoa_downloader.py "https://example.com/project"
```

Create an ICC offline viewer ZIP:

```bash
python cyoa_downloader.py "https://example.com/project" --icc
```

Create an ICC offline viewer folder:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder
```

Download a batch list:

```bash
python cyoa_downloader.py --list examples/batch_urls.csv --output downloads
```

Use more workers cautiously:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --threads 8
```

Run dependency diagnostics:

```bash
python cyoa_downloader.py --dependency-check
```

Run offline smoke tests:

```bash
python cyoa_downloader.py --self-test
```

---

## Batch file formats

TXT:

```text
https://example.com/cyoa-1
https://example.com/cyoa-2 | custom-name | website_folder
```

CSV:

```csv
url,filename,mode
https://example.com/cyoa-1,first_backup,website_folder
https://example.com/cyoa-2,second_backup,website_zip
```

Supported URL column names include `url`, `link`, `urls`, and `links`. Supported filename column names include `filename`, `name`, `output`, `title`, and `file`. The `mode` column is optional.

If a batch file has no URL column, the program reports it clearly instead of failing silently.

---

## FFMPEG and media tools

FFMPEG is not required for normal project downloads. It is only required for some audio/video extraction, conversion, or merge workflows used by optional media recovery tools.

Check FFMPEG:

```bash
ffmpeg -version
```

Install hints:

- Windows: install a static build from a trusted FFMPEG distributor, then add the `bin` folder to PATH.
- macOS: `brew install ffmpeg`.
- Debian/Ubuntu: `sudo apt install ffmpeg`.
- Fedora: `sudo dnf install ffmpeg` after enabling the appropriate multimedia repositories when needed.

Optional media recovery:

```bash
pip install yt-dlp
python -m pip install -U yt-dlp
```

More details are in [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) and [`docs/ADVANCED_FEATURES.md`](docs/ADVANCED_FEATURES.md).

---

## Theme, logo, and GUI consistency

The GUI uses the system theme by default:

```json
"theme_mode": "System"
```

Users can switch between **System**, **Dark**, and **Light** from the GUI settings. The selection is saved to the settings file. The dark toolbar divider uses a muted blue-grey line instead of a bright white separator so it remains visible without looking harsh.

Logo assets are kept in `assets/`:

```text
assets/logo-light.png
assets/logo-dark.png
assets/logo-source.png
```

The included logo assets are from the original release package. If the app cannot load external assets, it falls back safely so the GUI still opens.

---

## AI Assist status

AI Assist is optional. It is not needed for normal downloads and should remain off unless the user explicitly configures it.

Supported modes:

| Mode | Meaning |
| --- | --- |
| `off` | No AI calls. Recommended default. |
| `diagnostics` | Use AI only to help explain difficult detection failures. |
| `auto_fallback` | Use AI when standard extraction fails. |
| `aggressive_recovery` | More active recovery mode; advanced users only. |

Key storage options:

| Storage | Recommended for | Notes |
| --- | --- | --- |
| `session` | Most users | Key is used for the current run only. |
| `env` | Advanced users | Reads from environment variables. |
| `keyring` | Users with OS keyring support | Safer persistent storage when available. |
| `plain` | Local experiments only | Stores in settings; not recommended for shared machines. |

The logger redacts token-like values, but users should still avoid pasting secrets into issue reports.

---

## Development and validation

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the core gates:

```bash
python -m py_compile cyoa_downloader.py
python cyoa_downloader.py --self-test
pytest -q
ruff check cyoa_downloader.py --select F821
```

Maintainers should read [`docs/MAINTAINER_GUIDE.md`](docs/MAINTAINER_GUIDE.md) before changing downloader behavior, CLI flags, output formats, Offline Viewer Center, Manual Inject, userscript helper behavior, or settings compatibility.

---

## Compatibility rules

This repository treats the following behavior as compatibility-sensitive:

- existing CLI flags and aliases;
- output folder and ZIP structure;
- batch TXT/CSV/XLSX import format;
- Manual Inject and Offline Viewer Center workflow;
- local serve preview and bundled userscript helper behavior;
- settings import/export shape;
- dependency fallback behavior;
- `--dependency-check`, `--self-test`, and `--help` gates.

Changes should be additive unless there is a documented safety reason.

---

## License and credits

This project is released under the MIT License. See [`LICENSE`](LICENSE).

Third-party credits and bundled helper notes are listed in [`CREDITS.md`](CREDITS.md).
