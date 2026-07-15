<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="170">
  </picture>
</p>

<h1 align="center">CYOA Downloader</h1>

<p align="center">
  <em>Preserve interactive CYOA / ICC projects for offline reading and play — even after the original site goes offline.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg">
  <img alt="Release v1.0.5" src="https://img.shields.io/badge/Release-v1.0.5-orange.svg">
  <img alt="Interface: GUI and CLI" src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-purple.svg">
  <img alt="Self-test 37/37" src="https://img.shields.io/badge/Self--test-37%2F37-brightgreen.svg">
</p>

<p align="center">
  <a href="#getting-started">Getting started</a> ·
  <a href="#how-to-save-your-first-cyoa">First backup</a> ·
  <a href="#output-modes">Output modes</a> ·
  <a href="docs/CLI.md">CLI reference</a> ·
  <a href="docs/GETTING_STARTED.md">Full docs</a>
</p>

---

## Overview

An **interactive CYOA** is a choose-your-own-adventure, image- and point-buy project played in a web browser. These projects are fragile: they vanish when a site shuts down or an image host breaks.

**CYOA Downloader saves a complete, self-contained copy to your computer** — project data, images, fonts, audio, and the viewer itself — so you can open it offline at any time. Provide a link; the tool resolves, downloads, and packages everything into a folder or ZIP you can keep.

There are two ways to use it:

| Interface | For whom | What it looks like |
| --- | --- | --- |
| **Desktop app (GUI)** | Most users | A window with buttons — paste a link, pick a mode, click download. |
| **Command line (CLI)** | Power users | Scriptable commands for automation and large batches. |

No coding knowledge is required to use the desktop app. New users should follow [Getting started](#getting-started) below.

> **New here?** Open [Start here](START_HERE.md). It gives you one safe,
> copy-paste workflow: choose **ICC Folder**, download one URL, inspect the
> result, and only then explore advanced options.

### Table of contents

- [Getting started](#getting-started)
  - [Option 1 — Windows app (no setup)](#option-1--windows-app-no-setup)
  - [Option 2 — Run from Python](#option-2--run-from-python)
  - [Troubleshooting first-run issues](#troubleshooting-first-run-issues)
- [How to save your first CYOA](#how-to-save-your-first-cyoa)
- [GUI queue editing and list export](#gui-queue-editing-and-list-export)
- [Output modes](#output-modes)
- [Verifying a backup](#verifying-a-backup)
- [Main capabilities](#main-capabilities)
- [Documentation](#repository-layout)
- [Installation options](#dependency-install-options)
- [Command-line reference](#useful-cli-examples)
- [Compatibility & license](#compatibility-rules)
- [Support this project](#support-this-project)

---

## Getting started

Choose the path that fits your system. Most Windows users want Option 1.

### Option 1 — Windows app (no setup)

The fastest way to use the tool on Windows, with nothing to install.

1. Open the project's **[Releases page](../../releases)**.
2. Download the asset ending in **`-Windows-x64.zip`**.
3. **Right-click the ZIP → Extract All.** (Run it from the extracted folder, not from inside the zip preview.)
4. Double-click **`CYOA Downloader.exe`**.

The application opens immediately — continue to [How to save your first CYOA](#how-to-save-your-first-cyoa).

> **SmartScreen notice.** If Windows shows *“Windows protected your PC,”* click **More info → Run anyway**. This appears because the executable is not code-signed; the project is open-source and the source is in this repository.

### Option 2 — Run from Python

Use this on macOS or Linux, or on Windows when no executable is available. It is a copy-paste process you complete once.

**1. Install Python 3.10 or newer.**
Download from [python.org/downloads](https://www.python.org/downloads/). On Windows, enable **“Add Python to PATH”** during installation.

**2. Download the project.**
Use the green **`< > Code → Download ZIP`** button on this page and extract it, or `git clone` the repository.

**3. Open a terminal in the project folder.**
- **Windows:** open the folder, click the address bar, type `powershell`, press Enter.
- **macOS:** right-click the folder → *New Terminal at Folder*.
- **Linux:** open a terminal and `cd` into the folder.

**4. Run the setup block for your system.**

<details open>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```
</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```
</details>

The final command launches the application.

> **What this does.** The first line creates an isolated environment (`.venv`) so the project's dependencies stay separate from the rest of your system. The next lines install those dependencies. You perform this setup only once.

**Subsequent launches** do not repeat the install — open a terminal in the project folder and run:

| System | Commands |
| --- | --- |
| Windows | `.\.venv\Scripts\Activate.ps1` then `python cyoa_downloader.py` |
| macOS / Linux | `source .venv/bin/activate` then `python cyoa_downloader.py` |

> **Tip.** Save those two lines as `start.bat` (Windows) or `start.sh` (macOS/Linux) in the project folder for a one-click launcher.

### Troubleshooting first-run issues

| Symptom | Resolution |
| --- | --- |
| `python` / `py` not recognized | Python is not on PATH. Reinstall and enable **Add Python to PATH**, then reopen the terminal. |
| PowerShell blocks `Activate.ps1` | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, confirm with `Y`, then retry. |
| A dependency fails to install | Run `python cyoa_downloader.py --dependency-check`; anything marked optional is safe to skip. |
| Window does not open / Tkinter error (Linux) | Install Tk: `sudo apt install python3-tk`, then retry. |
| Anything else | See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md). |

Confirm a healthy setup at any time:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
```

---

## How to save your first CYOA

1. Launch the application (executable, or `python cyoa_downloader.py`).
2. **Paste the CYOA's web link** into the URL field.
3. Choose an output folder.
4. **Select a mode.** If unsure, choose **ICC Folder** — it produces a folder you can open and play offline. Prefer a single shareable file? Choose **ICC ZIP**.
5. Click **Download All** and follow the log.
6. When it finishes, open the output folder.
7. If some images failed, open `backup_report.txt`, then use **Retry Assets / Retry Images / Retry Audio** in the application.

To play an offline copy through the bundled viewer, open **Offline Viewer Center** and click **Auto-match**. If automatic matching fails, use **Inject** and select the project file manually.

> To confirm a backup is complete without re-downloading it, see [Verifying a backup](#verifying-a-backup).

---

## GUI queue editing and list export

In the GUI, click a row's mode badge, such as `auto`, and choose another mode
from the menu. The URL, filename, and queue position remain unchanged, so the
job does not need to be removed and re-added.

Use **Export List…** to save the current queue as CSV or TXT. The exported
fields are `url`, `filename`, and `mode`, and the file can be loaded again with
**Import List…**. Mode `auto` is preserved during export/import.

See the detailed [GUI Queue Guide](docs/GUI_QUEUE_GUIDE.md).

## Output modes

You control how each backup is packaged. Beginners can ignore the detail and use **ICC Folder**.

| Mode | Result | Recommended when |
| --- | --- | --- |
| **ICC Folder** ⭐ | Folder with the viewer and all assets | You want to open and play offline immediately. **Best default.** |
| **ICC ZIP** | The same, compressed into one file | You want a single file to store or share. |
| Embedded JSON | One data file | Small projects, or importing into another viewer. |
| ZIP | Project data + assets, compressed | Compact archive with files kept separate. |
| Both | Embedded JSON **and** ZIP | Maximum compatibility. |
| Pure Website (folder/zip) | A plain mirror of the site | Unusual or custom sites without standard project data. |
| CYOAP Vue (folder/zip) | A dedicated CYOAP Vue backup | Projects built with CYOAP Vue. |

CLI equivalents: `--icc-folder`, `--icc`, `--zip`, `--both`, `--pure-website-folder`, `--pure-website`, `--cyoap-vue-folder`, `--cyoap-vue-website`. See [Command-line reference](#useful-cli-examples).

---

## Verifying a backup

After a download, confirm a backup is complete **without downloading it again**:

```bash
python cyoa_downloader.py --verify "path/to/output_folder"
```

This read-only check reports missing referenced assets, empty files, and a broken or missing project file. It exits with status `0` when intact and `1` when a problem is found, so it works in scripts.

For the strongest check — detecting **corrupted or truncated** files, not only missing ones — record a checksum baseline once, then verify whenever you wish:

```bash
# 1. Record the baseline once (creates cyoa_manifest.json inside the folder):
python cyoa_downloader.py --verify "path/to/output_folder" --write-manifest

# 2. Verify at any later time:
python cyoa_downloader.py --verify "path/to/output_folder"
```

The checksum baseline is **opt-in** and is never written during a normal download, so default output folders are unchanged.


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
- `--self-test` for offline smoke validation, and `--verify` to integrity-check a finished backup.

---

## Repository layout

| Guide | Content |
| --- | --- |
| [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) | Installation, first run, first GUI backup, first CLI backup, FFMPEG and yt-dlp setup. |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | GUI panels, CLI workflows, batch files, output modes, Offline Viewer Center, Manual Inject. |
| [`docs/GUI_QUEUE_GUIDE.md`](docs/GUI_QUEUE_GUIDE.md) | Beginner steps for editing queue modes, filenames, importing, and CSV/TXT export. |
| [`docs/CLI.md`](docs/CLI.md) | Complete command-line reference: every flag grouped by category, with examples and exit codes. |
| [`docs/ADVANCED_FEATURES.md`](docs/ADVANCED_FEATURES.md) | AI Assist, Cloudflare handling, proxies, DNS, HTTP/2, media recovery, theme/logo behavior, serve tools. |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Practical fixes for dependency errors, failed URLs, missing assets, GUI issues, FFMPEG/yt-dlp, and batch problems. |
| [`docs/FAQ.md`](docs/FAQ.md) | Short answers to the most common questions about modes, problems, backups, and AI Assist. |
| [`docs/MAINTAINER_GUIDE.md`](docs/MAINTAINER_GUIDE.md) | Project structure, tests, release gates, documentation rules, and compatibility requirements. |

Project meta files: [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md),
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), [`CHANGELOG.md`](CHANGELOG.md), and
[`CREDITS.md`](CREDITS.md).

---

## Dependency install options

Most users only need the normal runtime dependencies. `requirements.txt` is
the beginner install, `requirements-optional.txt` contains heavier features,
and `requirements-dev.txt` adds test/lint tools for maintainers.

### Normal user install

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Advanced optional feature install

Use this only for heavier recovery features such as Cloudflare fallback, gallery fallback, safer key storage, browser-cookie helpers, DNS helpers, browser fallback, spreadsheet import, media recovery, and HTTP/2:

```bash
pip install -r requirements-optional.txt
```

If you use Playwright-based browser fallback, install Chromium separately:

```bash
python -m playwright install chromium
```

### Developer / CI install

```bash
pip install -r requirements-dev.txt
```

### Complete local setup

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-optional.txt
pip install -r requirements-dev.txt
python -m playwright install chromium
```

FFMPEG is installed through the operating system, not pip. See the FFMPEG section below.


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

- Windows with winget:

  ```powershell
  winget install Gyan.FFmpeg
  ffmpeg -version
  ```

- Windows manual install: download a trusted static build, extract it, add the `bin` folder to PATH, reopen the terminal, then run `ffmpeg -version`.
- macOS:

  ```bash
  brew install ffmpeg
  ffmpeg -version
  ```

- Debian/Ubuntu:

  ```bash
  sudo apt update
  sudo apt install ffmpeg
  ffmpeg -version
  ```

- Fedora:

  ```bash
  sudo dnf install ffmpeg
  ffmpeg -version
  ```

Optional media recovery:

```bash
pip install -U yt-dlp
```

More details are in [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) and [`docs/ADVANCED_FEATURES.md`](docs/ADVANCED_FEATURES.md).



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

## Contributing and community

Contributions are welcome. Because the tool handles untrusted remote content and writes files to
disk, small, tested, backward-compatible changes are preferred over broad rewrites.

- **Report a bug or request a feature** using the issue templates in the repository.
- **Open a pull request** following [`CONTRIBUTING.md`](CONTRIBUTING.md) and the PR checklist.
- **Security concerns** should be reported privately per [`SECURITY.md`](SECURITY.md).
- All participation is governed by the [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

Before submitting changes, run the gate suite:

```bash
python -m py_compile cyoa_downloader.py
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test        # expect 37/37
ruff check cyoa_downloader.py --select F821,F811,F601
pytest -q
```

---

## Support this project

CYOA Downloader is free and open-source, and it stays that way. It is built and maintained in
spare time, and development has real costs — including the AI tooling used to write, debug, and
document the codebase. If the tool has been useful to you, a small contribution helps cover those
costs and keeps the project actively maintained. This is entirely optional and never unlocks
features; everything is free for everyone.

- **Bitcoin (BTC):**

  ```
  1Kz5LChzNXxzQbGTjpWQx66mQ4zJj4yavB
  ```

Thank you for considering it — and just as valuable: starring the repo, reporting bugs, and
sharing the project all genuinely help.

---

## License and credits

This project is released under the MIT License. See [`LICENSE`](LICENSE).

Third-party credits and bundled helper notes are listed in [`CREDITS.md`](CREDITS.md).
