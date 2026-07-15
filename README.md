<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="170">
  </picture>
</p>

<h1 align="center">CYOA Downloader</h1>

<p align="center">
  Preserve interactive CYOA / ICC projects for offline reading and play.
</p>

<p align="center">
  <img alt="Release v1.0.5" src="https://img.shields.io/badge/Release-v1.0.5-orange.svg">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg">
  <img alt="GUI and CLI" src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-purple.svg">
</p>

> **New here?** Read [Start here](START_HERE.md). It is the shortest path to
> your first working backup.

## What this program does

CYOA Downloader takes a CYOA website URL and saves a local copy containing the
project data, images, fonts, audio references, and—when requested—the offline
viewer. You can create a normal folder, a ZIP, or a project-data snapshot.

It has two interfaces:

| Interface | Best for |
| --- | --- |
| **GUI** | Beginners, queue editing, retry buttons, viewer tools, and visual settings. |
| **CLI** | Repeatable commands, batch lists, scripts, and diagnostics. |

The downloader cannot guarantee access to a site that requires login, blocks
automation, has deleted assets, or depends on a live private API. It is intended
for content you are allowed to archive.

## Fastest first run

### Windows executable

1. Open the [Releases page](../../releases).
2. Download the `-Windows-x64.zip` asset.
3. Extract the ZIP.
4. Run `CYOA Downloader.exe`.

### Run from Python

Install Python 3.10 or newer, open a terminal in this repository, then run:

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If PowerShell blocks activation, run this once and retry:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## First GUI backup

1. Paste one CYOA URL into the URL field.
2. Choose an output folder, or keep the default.
3. Select **ICC Folder**.
4. Click **Add URL**, then **Download All**.
5. Wait for the final status.
6. Click **Open Folder** and inspect the result.

**ICC Folder** is the recommended first mode because it is easy to inspect and
retry. Use **ICC ZIP** when you want one file to store or share.

## Queue editing and export

The GUI queue is editable after a URL has been added:

- edit the filename directly in the row;
- drag the handle to reorder a job;
- click the mode badge to switch from `auto` to another mode;
- click `×` to remove one row;
- click **Export List…** to save the queue as CSV or TXT.

Exported lists contain `url`, `filename`, and `mode`, and can be loaded again
with **Import List…**. See the [GUI Queue Guide](docs/GUI_QUEUE_GUIDE.md).

## Choosing an output mode

| Mode | Use it when |
| --- | --- |
| **ICC Folder** | You want a playable, inspectable offline folder. **Best first choice.** |
| **ICC ZIP** | You want the same viewer package as one ZIP file. |
| **Embedded JSON** | You want a compact project-data snapshot. |
| **ZIP** | You want project data and external assets in an archive. |
| **Both** | You want embedded JSON and a ZIP backup together. |
| **Pure Website Folder/ZIP** | Normal project detection fails or the site is a custom viewer. |
| **CYOAP Vue Folder/ZIP** | The site uses the CYOAP Vue project structure. |

When troubleshooting, prefer a folder over a ZIP because you can see which
file is missing.

## What to do after a download

Look in the output folder for:

| File | Meaning |
| --- | --- |
| `backup_report.txt` | Main summary and failed-file details. |
| `failed_assets.txt` | Asset URLs that need another attempt. |
| `failed_images.txt` | Image URLs used by the image retry workflow. |
| `failed_urls.txt` | URL-level failures from a batch. |
| `cyoa_downloader.log` | Detailed technical log. |

Use **Retry Assets**, **Retry Images**, or **Retry Audio** from the GUI when
appropriate. If a viewer is blank when opened with `file://`, use **Serve** to
open it through `http://localhost`.

To check an existing output without downloading again:

```bash
python cyoa_downloader.py --verify "path/to/output_folder"
```

## CLI quick start

```bash
# Check installation
python cyoa_downloader.py --dependency-check

# Download one inspectable folder
python cyoa_downloader.py "https://example.com/cyoa/" --icc-folder --output downloads

# Download one shareable ZIP
python cyoa_downloader.py "https://example.com/cyoa/" --icc --output downloads

# Download several URLs from a list
python cyoa_downloader.py --list examples/batch_urls.csv --icc-folder --output downloads
```

Always quote URLs. Read the [CLI reference](docs/CLI.md) for all flags and
copy-paste recipes.

## Requirements

The normal install is intentionally small:

```bash
pip install -r requirements.txt
```

Install optional features only when you need them:

```bash
pip install -r requirements-optional.txt
```

Optional features include XLSX/XLS batch files, YouTube/SoundCloud recovery,
HTTP/2 deep scanning, Cloudflare helpers, browser fallback, gallery fallback,
and safer AI key storage. FFMPEG is installed separately through the operating
system; it is not required for normal image/viewer downloads.

Maintainers can install the test/lint tools with:

```bash
pip install -r requirements-dev.txt
```

## Documentation map

| Guide | Start here when you need… |
| --- | --- |
| [Start here](START_HERE.md) | The shortest beginner workflow. |
| [Getting Started](docs/GETTING_STARTED.md) | Installation, verification, and first GUI/CLI run. |
| [User Guide](docs/USER_GUIDE.md) | Normal GUI use, modes, reports, retry, viewers, settings, and batch import. |
| [GUI Queue Guide](docs/GUI_QUEUE_GUIDE.md) | Queue mode changes, filenames, import, and CSV/TXT export. |
| [CLI reference](docs/CLI.md) | CLI commands, flags, batch lists, diagnostics, and exit codes. |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Missing assets, blocked sites, media, GUI, and network failures. |
| [Advanced Features](docs/ADVANCED_FEATURES.md) | AI, Cloudflare, proxy/DNS, HTTP/2, browser fallback, and media recovery. |
| [Maintainer Guide](docs/MAINTAINER_GUIDE.md) | Code changes, compatibility rules, tests, and release packaging. |

## Development checks

```bash
python -m py_compile cyoa_downloader.py
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python -m pytest -q
```

## License and safe use

This project is released under the MIT License. See [LICENSE](LICENSE) and
[CREDITS.md](CREDITS.md).

Archive only content you are permitted to save. Respect creators, site terms,
copyright, private content, login boundaries, and access restrictions. Never
share cookies, API keys, authorization headers, or private URLs in bug reports.
