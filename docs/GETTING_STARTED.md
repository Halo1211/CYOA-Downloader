# Getting Started

This guide is the first document new users should read. It explains how to install **CYOA Downloader**, verify the setup, start the GUI, run the first CLI backup, understand the most common command-line options, and prepare optional tools such as FFMPEG and `yt-dlp`.

CYOA Downloader is designed to preserve interactive CYOA projects by downloading project data, images, fonts, audio/video references where supported, and viewer files when an offline viewer mode is selected.

---

## Documentation map

All links below assume this file is inside the `docs/` directory.

| Document | Read this when you need |
| --- | --- |
| [Getting Started](./GETTING_STARTED.md) | Installation, first run, first GUI backup, first CLI backup, dependency checks, and beginner CLI tables. |
| [User Guide](./USER_GUIDE.md) | Daily GUI/CLI workflows, output modes, reports, retry tools, batch import, Offline Viewer Center, and Manual Inject. |
| [Advanced Features](./ADVANCED_FEATURES.md) | AI Assist, Cloudflare handling, proxy/DNS/HTTP2, media fallback, theme/logo behavior, userscript helper, and advanced recovery options. |
| [Troubleshooting](./TROUBLESHOOTING.md) | Setup failures, missing project data, failed assets, batch errors, GUI visual issues, Cloudflare problems, and bug-report preparation. |
| [Maintainer Guide](./MAINTAINER_GUIDE.md) | Repository structure, compatibility rules, test gates, documentation rules, release checklist, and packaging policy. |

Recommended reading order:

1. **Getting Started** for installation and first backup.
2. **User Guide** when you want to understand the GUI and output modes.
3. **Troubleshooting** when something fails.
4. **Advanced Features** only when a difficult site or media source needs extra handling.
5. **Maintainer Guide** if you are editing the repository or preparing a release.

---

## 1. Requirements

| Requirement | Required? | Purpose |
| --- | --- | --- |
| Python 3.10+ | Required | Runs the downloader. |
| Internet access | Required | Downloads projects, viewer files, images, fonts, and optional media. |
| Disk space | Required | Large projects can contain many assets. |
| Tkinter | Required for GUI | Used by the GUI. Usually bundled with Python on Windows/macOS; may be separate on Linux. |
| `requests`, `urllib3`, `beautifulsoup4` | Required | HTTP requests and HTML/project parsing. |
| `tldextract`, `json5` | Required/recommended | Domain handling and tolerant project parsing. |
| `pandas`, `openpyxl` | Optional for batch spreadsheets | Needed for XLSX/XLS batch imports. |
| `customtkinter` | Optional but recommended | Modern GUI appearance. |
| `pillow` | Optional but recommended | Logo/image handling in the GUI. |
| `httpx[http2]` | Optional | HTTP/2 deep-scan fetching when enabled. |
| `yt-dlp` | Optional | Supported media extraction workflows. |
| FFMPEG | Optional | Media merge/conversion workflows, especially with `yt-dlp`. |

Normal image and viewer backups do **not** require FFMPEG. Missing optional tools should produce warnings, not crash the normal downloader.

---

## 2. Install on Windows

Open PowerShell in the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

If `py` is not available:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Check Python version:

```powershell
python --version
```

---

## 3. Install on macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If Python is too old, install a newer Python build first, then repeat the virtual environment steps.

---

## 4. Install on Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

On minimal Linux installations, install Tkinter separately:

```bash
sudo apt update
sudo apt install python3-tk
```

---

## 5. Verify installation

Run these commands from the repository root:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

| Command | Expected result |
| --- | --- |
| `--dependency-check` | Lists required and optional dependency status, including FFMPEG and optional media tools. |
| `--self-test` | Runs offline sanity checks. It should not need a live website. |
| `--help` | Prints all CLI flags and exits. |

If `--dependency-check` reports an optional dependency as missing, install it only if you need that feature.

---

## 6. Start the GUI

```bash
python cyoa_downloader.py
```

Running without arguments opens the GUI. To force GUI mode:

```bash
python cyoa_downloader.py --gui
```

First GUI backup:

1. Paste the CYOA URL.
2. Choose an output mode.
3. Click **Download All**.
4. Wait until the log finishes.
5. Open the output folder.
6. Read the generated report.
7. Retry failed assets if needed.

Recommended beginner settings:

| Setting | Recommended value | Why |
| --- | --- | --- |
| Theme | System | Follows the OS and avoids forcing dark/light mode. |
| Workers | 4 | Stable default for most sites. |
| Wait time | 60 seconds | Good default after rate limits. |
| Deep scan | Enabled | Finds assets referenced inside JavaScript/CSS. |
| yt-dlp | Enabled only when needed | Not necessary for normal image-only backups. |
| AI Assist | Off or Diagnostics | Avoids unnecessary API calls and cost. |

For detailed GUI workflows, read [User Guide](./USER_GUIDE.md).

---

## 7. CLI learning path

Do not start with every advanced flag. Use this progression:

1. Run `python cyoa_downloader.py --help`.
2. Run `python cyoa_downloader.py --dependency-check`.
3. Try one URL with the default command.
4. Try `--icc-folder` for an inspectable offline viewer.
5. Try batch mode after one single URL works.
6. Add proxy, Cloudflare, media, or AI options only when the basic workflow fails.

---

## 8. Quick command table

| Goal | Command | Explanation |
| --- | --- | --- |
| Open GUI | `python cyoa_downloader.py` | Starts the graphical interface. |
| Force GUI | `python cyoa_downloader.py --gui` | Opens GUI explicitly. |
| Show help | `python cyoa_downloader.py --help` | Shows every available CLI flag. |
| Check dependencies | `python cyoa_downloader.py --dependency-check` | Reports required/optional dependencies. |
| Run self-test | `python cyoa_downloader.py --self-test` | Runs offline sanity checks. |
| Download one project | `python cyoa_downloader.py "https://example.com/project"` | Runs a normal single-project backup. |
| Choose output folder | `python cyoa_downloader.py "https://example.com/project" --output downloads` | Writes output to `downloads/`. |
| ICC ZIP | `python cyoa_downloader.py "https://example.com/project" --icc` | Creates an ICC-style ZIP when supported. |
| ICC folder | `python cyoa_downloader.py "https://example.com/project" --icc-folder` | Creates an inspectable ICC-style folder. |
| Batch download | `python cyoa_downloader.py --list examples/batch_urls.csv --output downloads` | Reads multiple URLs from a batch file. |
| Serve preview | `python cyoa_downloader.py "https://example.com/project" --icc-folder --serve` | Serves output locally after download. |

Always quote URLs. Shells can misread `?`, `&`, and `#` when URLs are not quoted.

---

## 9. Output mode guide

| Output mode | Flag | Best for | Output style | Notes |
| --- | --- | --- | --- | --- |
| Default backup | no mode flag | First test | Auto/default | Good initial command. |
| External image ZIP | `--zip` | Legacy external-image backup | ZIP | Keeps older workflow compatibility. |
| Both | `--both` | Maximum compatibility | Multiple outputs | Useful when unsure. |
| ICC ZIP | `--icc` | Shareable offline viewer | ZIP | Good final package format. |
| ICC folder | `--icc-folder` | Inspectable offline viewer | Folder | Best troubleshooting mode. |
| Pure website ZIP | `--pure-website` | Custom site fallback | ZIP | Skips normal project detection. |
| Pure website folder | `--pure-website-folder` | Custom viewer debugging | Folder | Easier to inspect than ZIP. |
| CYOAP Vue auto | `--cyoap-vue` | CYOAP Vue sites | Auto | Dedicated CYOAP handling first. |
| CYOAP Vue ZIP | `--cyoap-vue-website` | CYOAP Vue packaged backup | ZIP | Dedicated website output. |
| CYOAP Vue folder | `--cyoap-vue-folder` | CYOAP Vue debugging | Folder | Best for path checking. |

---

## 10. CLI reference table

The complete live reference is always:

```bash
python cyoa_downloader.py --help
```

### 10.1 Basic input and output

| Option | Example | Purpose |
| --- | --- | --- |
| `url` | `python cyoa_downloader.py "https://example.com/project"` | Positional CYOA URL. |
| `filename` | `python cyoa_downloader.py "https://example.com/project" my_backup` | Optional output filename. |
| `-u`, `--url` | `python cyoa_downloader.py --url "https://example.com/project"` | URL option form. |
| `-o`, `--output` | `python cyoa_downloader.py "URL" --output downloads` | Output directory. |
| `-L`, `--list` | `python cyoa_downloader.py --list batch.csv --output downloads` | Batch import file or remote CSV source. |

### 10.2 Backup modes

| Option | Example | Purpose |
| --- | --- | --- |
| `-z`, `--zip` | `python cyoa_downloader.py "URL" --zip` | ZIP with external images. |
| `-b`, `--both` | `python cyoa_downloader.py "URL" --both` | Embedded JSON plus ZIP. |
| `--icc` | `python cyoa_downloader.py "URL" --icc` | Full ICC viewer ZIP. |
| `--icc-folder` | `python cyoa_downloader.py "URL" --icc-folder` | Full ICC viewer folder. |
| `--pure-website` | `python cyoa_downloader.py "URL" --pure-website` | Pure website ZIP. |
| `--pure-website-folder` | `python cyoa_downloader.py "URL" --pure-website-folder` | Pure website folder. |
| `--cyoap-vue` | `python cyoa_downloader.py "URL" --cyoap-vue` | Dedicated CYOAP Vue handling. |
| `--cyoap-vue-website` | `python cyoa_downloader.py "URL" --cyoap-vue-website` | CYOAP Vue ZIP. |
| `--cyoap-vue-folder` | `python cyoa_downloader.py "URL" --cyoap-vue-folder` | CYOAP Vue folder. |

### 10.3 Performance and scanning

| Option | Example | Purpose |
| --- | --- | --- |
| `-f`, `--fonts` | `python cyoa_downloader.py "URL" --icc-folder --fonts` | Download/localize fonts. |
| `-a`, `--analyse-fonts` | `python cyoa_downloader.py "URL" --analyse-fonts` | Font analysis only. |
| `-t`, `--threads`, `--workers` | `python cyoa_downloader.py "URL" --workers 8` | Worker count. |
| `-w`, `--wait-time`, `--wait` | `python cyoa_downloader.py "URL" --wait 120` | Wait after rate limits. |
| `--bandwidth` | `python cyoa_downloader.py "URL" --bandwidth 512` | KB/s bandwidth limit. |
| `--no-deep-scan` | `python cyoa_downloader.py "URL" --no-deep-scan` | Disable JS/CSS asset scan. |
| `--http2` | `python cyoa_downloader.py "URL" --http2` | Enable HTTP/2 deep-scan fetches. |
| `--no-http2` | `python cyoa_downloader.py "URL" --no-http2` | Disable HTTP/2 behavior. |

### 10.4 Network and Cloudflare

| Option | Example | Purpose |
| --- | --- | --- |
| `--proxy` | `python cyoa_downloader.py "URL" --proxy http://127.0.0.1:7890` | Manual proxy URL. |
| `--proxy-mode` | `python cyoa_downloader.py "URL" --proxy-mode disabled` | Proxy behavior: `inherit_env`, `manual`, `disabled`. |
| `--dns` | `python cyoa_downloader.py "URL" --dns 1.1.1.1` | DNS override. |
| `--bebasdns` | `python cyoa_downloader.py "URL" --bebasdns unfiltered` | BebasDNS DoH preset. |
| `--cloudflare` | `python cyoa_downloader.py "URL" --cloudflare auto` | Cloudflare handling mode. |
| `--cf-bypass`, `--cloudscraper` | `python cyoa_downloader.py "URL" --cf-bypass` | Legacy cloudscraper alias. |
| `--flaresolverr-url` | `python cyoa_downloader.py "URL" --cloudflare flaresolverr --flaresolverr-url http://localhost:8191/v1` | FlareSolverr API endpoint. |
| `--flaresolverr-test` | `python cyoa_downloader.py --flaresolverr-test` | Test FlareSolverr configuration. |

Advanced network options are explained in [Advanced Features](./ADVANCED_FEATURES.md).

### 10.5 Media and optional extractors

| Option | Example | Purpose |
| --- | --- | --- |
| `--no-ytdlp` | `python cyoa_downloader.py "URL" --no-ytdlp` | Disable yt-dlp media recovery. |
| `--gallery-dl` | `python cyoa_downloader.py "URL" --gallery-dl smart` | Optional gallery-dl fallback. |
| `--gallery-dl-path` | `python cyoa_downloader.py "URL" --gallery-dl-path gallery-dl` | Set gallery-dl path. |
| `--gallery-dl-config` | `python cyoa_downloader.py "URL" --gallery-dl-config config.json` | Set gallery-dl config. |
| `--no-selenium` | `python cyoa_downloader.py "URL" --no-selenium` | Disable Selenium fallback. |
| `--itch` | `python cyoa_downloader.py "URL" --itch` | Enable itch.io downloader. |
| `--itch-test` | `python cyoa_downloader.py --itch-test` | Test itch.io backend. |

### 10.6 AI Assist

| Option | Example | Purpose |
| --- | --- | --- |
| `--ai-mode` | `python cyoa_downloader.py "URL" --ai-mode diagnostics` | AI mode: `off`, `diagnostics`, `auto_fallback`, `aggressive_recovery`. |
| `--ai-provider` | `python cyoa_downloader.py "URL" --ai-provider openai` | Select AI provider preset. |
| `--ai-key` | `python cyoa_downloader.py "URL" --ai-key YOUR_KEY` | API key for this run. |
| `--ai-key-storage` | `python cyoa_downloader.py "URL" --ai-key-storage keyring` | Key storage mode. |
| `--ai-model` | `python cyoa_downloader.py "URL" --ai-model gpt-4.1-mini` | Provider model. |
| `--ollama-url` | `python cyoa_downloader.py "URL" --ai-provider ollama --ollama-url http://localhost:11434` | Ollama base URL. |
| `--ai-max-calls` | `python cyoa_downloader.py "URL" --ai-max-calls 3` | Max AI calls per run. |
| `--ai-clear-key` | `python cyoa_downloader.py --ai-clear-key` | Clear saved AI key. |

Read [Advanced Features](./ADVANCED_FEATURES.md) before enabling AI recovery. Normal backups do not require AI.

### 10.7 Diagnostics and settings

| Option | Example | Purpose |
| --- | --- | --- |
| `--cyoa-manager` | `python cyoa_downloader.py "URL" --cyoa-manager` | Optional CYOA Manager integration. |
| `--serve` | `python cyoa_downloader.py "URL" --icc-folder --serve` | Serve output locally. |
| `--serve-port` | `python cyoa_downloader.py "URL" --serve --serve-port 8080` | Select local serve port. |
| `--language` | `python cyoa_downloader.py --gui --language en` | Set language preference. |
| `--dependency-check` | `python cyoa_downloader.py --dependency-check` | Dependency report. |
| `--userscript-info` | `python cyoa_downloader.py --userscript-info` | Userscript helper credit/details. |
| `--self-test` | `python cyoa_downloader.py --self-test` | Offline sanity checks. |
| `--export-settings` | `python cyoa_downloader.py --export-settings settings.json` | Export settings with secrets redacted. |
| `--import-settings` | `python cyoa_downloader.py --import-settings settings.json` | Import non-secret settings. |
| `--gui` | `python cyoa_downloader.py --gui` | Force GUI. |

---

## 11. First CLI backup examples

Default:

```bash
python cyoa_downloader.py "https://example.com/project"
```

ICC folder:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder
```

ICC ZIP:

```bash
python cyoa_downloader.py "https://example.com/project" --icc
```

Custom output folder:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --output downloads
```

Serve result:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --serve
```

---

## 12. Batch download quick start

### TXT format

```text
https://example.com/cyoa-1
https://example.com/cyoa-2 | second_backup | website_folder
```

### CSV format

```csv
url,filename,mode
https://example.com/cyoa-1,first_backup,website_folder
https://example.com/cyoa-2,second_backup,website_zip
```

Run:

```bash
python cyoa_downloader.py --list examples/batch_urls.csv --output downloads
```

Supported URL columns: `url`, `link`, `urls`, `links`.

Supported filename columns: `filename`, `name`, `output`, `title`, `file`.

Supported mode columns: `mode`, `output_mode`, `type`.

For batch troubleshooting, see [Troubleshooting](./TROUBLESHOOTING.md).

---

## 13. FFMPEG setup

Check:

```bash
ffmpeg -version
```

Windows:

1. Download a trusted static FFMPEG build.
2. Extract it.
3. Add the `bin` folder to PATH.
4. Open a new terminal.
5. Run `ffmpeg -version`.

macOS:

```bash
brew install ffmpeg
ffmpeg -version
```

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install ffmpeg
ffmpeg -version
```

---

## 14. yt-dlp setup

Install or update:

```bash
pip install -U yt-dlp
```

Then run:

```bash
python cyoa_downloader.py --dependency-check
```

---

## 15. Updating dependencies

Runtime dependencies:

```bash
python -m pip install --upgrade pip
pip install -U -r requirements.txt
```

Development tools:

```bash
pip install -U -r requirements-dev.txt
```

Validate after update:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python -m py_compile cyoa_downloader.py
```

---

## 16. Common beginner mistakes

| Problem | Likely cause | Fix |
| --- | --- | --- |
| `python` opens Microsoft Store | Python or PATH problem | Install Python from python.org or use `py -3`. |
| `.venv` cannot activate on Windows | PowerShell execution policy | Use `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| GUI does not open on Linux | Tkinter missing | Install `python3-tk`. |
| FFMPEG warning | FFMPEG not in PATH | Install FFMPEG and reopen the terminal. |
| Batch reads zero URLs | Missing URL column | Use `url` or `link`. |
| Viewer opens but assets are missing | Dynamic JS/CSS paths or blocked assets | Use `--icc-folder`, keep deep scan enabled, read reports, retry failed assets. |
| Rate-limit errors | Too many parallel requests | Use `--workers 2 --wait 120`. |

For detailed error handling, read [Troubleshooting](./TROUBLESHOOTING.md).

---

## 17. Good first bug report data

Before opening an issue, collect:

1. Operating system.
2. Python version.
3. Exact command.
4. Output of `--dependency-check`.
5. Output of `--self-test`.
6. GUI or CLI workflow.
7. Output mode.
8. Log/report files.
9. Minimal URL example if safe to share.
10. Whether `--workers 2 --wait 120` changes the result.

Do not share API keys, cookies, tokens, or private URLs.
