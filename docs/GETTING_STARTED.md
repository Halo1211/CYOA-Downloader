# Getting Started

This is the installation guide for CYOA Downloader v1.0.5. If you only want
to download one CYOA, use [Start here](../START_HERE.md) and return to this
page only when you need setup details.

## The beginner path

1. Install Python 3.10+ or download the Windows executable.
2. Install the normal requirements.
3. Run the dependency check.
4. Open the GUI.
5. Download one URL using **ICC Folder**.

Do not install optional AI, Cloudflare, browser, media, or spreadsheet tools
until you need them.

## Option A: Windows executable

This is the easiest Windows option:

1. Open the [GitHub Releases page](https://github.com/Halo1211/CYOA-Downloader/releases).
2. Download the asset ending in `-Windows-x64.zip`.
3. Right-click it and choose **Extract All**.
4. Open the extracted folder and run `CYOA Downloader.exe`.

The executable may trigger Windows SmartScreen because it is not code-signed.
The ZIP contains the Python application and packaged Python modules. Open
**Diagnostics** after starting it; external tools such as FFmpeg, Deno,
Chrome/Chromium, Selenium drivers, Playwright Chromium, and unrar/7-Zip are
reported separately because they are not silently bundled.
Confirm that it came from the project release page before choosing **More info
→ Run anyway**.

## Option B: Python on Windows

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/).
During installation, enable **Add Python to PATH**.

Open PowerShell in the repository folder and run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `py` is not recognized, use `python` instead:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Option C: Python on macOS/Linux

Install Python 3.10 or newer, open a terminal in the repository folder, and
run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

On Debian/Ubuntu, install Tkinter if the GUI cannot start:

```bash
sudo apt update
sudo apt install python3-tk
```

## Check the installation

Run these commands from the repository root while the virtual environment is
active:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

What they mean:

| Command | Meaning |
| --- | --- |
| `--dependency-check` | Shows which normal and optional tools are available. |
| `--self-test` | Runs offline sanity checks; it does not download a website. |
| `--help` | Prints the complete CLI flag list. |

An optional dependency warning is safe to ignore unless you need that feature.

## Start the GUI

```bash
python cyoa_downloader.py
```

Running without arguments opens the GUI. `python cyoa_downloader.py --gui`
does the same explicitly.

For the first download:

1. Paste a URL.
2. Keep the default output folder or choose another one.
3. Select **ICC Folder**.
4. Click **Add URL**, then **Download All**.
5. Click **Open Folder** when it finishes.

Recommended beginner settings:

| Setting | First value | Reason |
| --- | --- | --- |
| Mode | ICC Folder | Easy to inspect and retry. |
| Workers/threads | 4 | Stable default. |
| Wait/retry delay | 60 seconds | Safer after rate limits. |
| Deep scan | On | Finds common JavaScript/CSS assets. |
| AI Assist | Off | No key or cost is needed for normal downloads. |

See [User Guide](./USER_GUIDE.md) for queue editing, output modes, reports,
retry buttons, Offline Viewer Center, and Manual Inject.

## Start the CLI

The CLI is useful for scripts and repeatable downloads. Start with one of these
commands:

```bash
# Inspectable offline folder
python cyoa_downloader.py "https://example.com/cyoa/" --icc-folder --output downloads

# One shareable ZIP
python cyoa_downloader.py "https://example.com/cyoa/" --icc --output downloads

# Batch list
python cyoa_downloader.py --list examples/batch_urls.csv --icc-folder --output downloads
```

Read the [CLI reference](./CLI.md) for flag details and batch formats.

## Optional requirements

The normal install includes the GUI and standard downloader. Install optional
Python packages only when needed:

```bash
pip install -r requirements-optional.txt
```

This adds support for:

- XLSX/XLS batch import (`pandas`, `openpyxl`);
- YouTube/SoundCloud recovery (`yt-dlp[default]`, including `yt-dlp-ejs`);
- HTTP/2 deep scanning (`httpx[http2]`);
- Cloudflare, gallery, browser, DNS, cookie, and AI-key helpers.

FFmpeg is a separate operating-system program. It is needed for some media
conversion workflows, not for normal JSON/image/viewer backups:

```bash
ffmpeg -version
```

Playwright also needs its browser after the Python package is installed:

```bash
python -m playwright install chromium
```

YouTube extraction also needs a supported JavaScript runtime. Install Deno and
confirm it is visible with `deno --version`. If Deno is outside `PATH`, set
`CYOA_YTDLP_DENO` to the full path of `deno.exe` before launching the program.

When YouTube reports that account cookies are no longer valid, export a fresh
Netscape-format cookie file. Cookies are session credentials; never commit or
share the file.

## What to do when a download is incomplete

1. Open `backup_report.txt` in the output folder.
2. Run `--verify` if the output is a folder.
3. Use **Retry Assets**, **Retry Images**, or **Retry Audio** in the GUI.
4. Lower workers to `2` and increase wait time to `120` seconds.
5. Read [Troubleshooting](./TROUBLESHOOTING.md).

If a viewer is blank when opened by double-clicking `index.html`, use the GUI
**Serve** button or the CLI `--serve` option. Modern viewers often need
`http://localhost` instead of `file://`.

## Documentation map

| Need | Document |
| --- | --- |
| Shortest first run | [Start here](../START_HERE.md) |
| Normal GUI workflow | [User Guide](./USER_GUIDE.md) |
| Queue mode/edit/export | [GUI Queue Guide](./GUI_QUEUE_GUIDE.md) |
| CLI commands and flags | [CLI reference](./CLI.md) |
| Optional recovery tools | [Advanced Features](./ADVANCED_FEATURES.md) |
| Errors and failed assets | [Troubleshooting](./TROUBLESHOOTING.md) |
| Code, tests, and releases | [Maintainer Guide](./MAINTAINER_GUIDE.md) |
