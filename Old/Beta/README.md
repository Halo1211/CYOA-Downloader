# CYOA Downloader v7.3.9

CYOA Downloader is a Python GUI and CLI tool for downloading, packaging, and preserving Interactive CYOA projects for offline playback.

It supports ICC Plus, ICC Remix, ICC Original style projects, cyoap_vue projects, CYOA Manager workflows, and many custom React or Vue based CYOA websites when their project data and assets can be detected.

> This project is experimental, AI-assisted, and feature rich. Use it carefully. Keep backups. Read `cyoa_downloader.log` and `backup_report.txt` when something breaks.

## Table of contents

- [What this tool does](#what-this-tool-does)
- [Main features](#main-features)
- [Supported output modes](#supported-output-modes)
- [Installation](#installation)
- [Optional system dependencies](#optional-system-dependencies)
- [Quick start](#quick-start)
- [CLI usage](#cli-usage)
- [Batch download](#batch-download)
- [Cloudflare and FlareSolverr](#cloudflare-and-flaresolverr)
- [BebasDNS and DNS options](#bebasdns-and-dns-options)
- [HTTP/2 support](#http2-support)
- [AI Assist](#ai-assist)
- [gallery-dl fallback](#gallery-dl-fallback)
- [Local preview server](#local-preview-server)
- [Reports and logs](#reports-and-logs)
- [Troubleshooting](#troubleshooting)
- [Recommended repository structure](#recommended-repository-structure)
- [Known limitations](#known-limitations)
- [Ethical use](#ethical-use)
- [License](#license)

## What this tool does

CYOA Downloader helps archive Interactive CYOA projects so they can be opened later without depending on the original website staying online.

It can:

- Download Interactive CYOA projects from direct URLs.
- Resolve many `cyoa.cafe` links.
- Detect ICC compatible `project.json`, `project.txt`, or `project.zip` files.
- Download project images, audio, fonts, scripts, styles, and website assets.
- Package projects into embedded JSON, ZIP, website folder, website ZIP, or both formats.
- Download cyoap_vue projects through `dist/platform.json` and node files.
- Inject downloaded data into compatible offline viewer packages.
- Import downloaded projects into CYOA Manager when its SQLite database is available.
- Run from a desktop GUI or CLI.
- Process single URLs or batch queue files.
- Run a local no-cache preview server for browser playback.

## Main features

### Download and packaging

- Website Folder and Website ZIP output.
- Embedded JSON output with base64 image embedding.
- ZIP output with `project.json` plus asset folders.
- Both mode for embedded JSON plus ZIP.
- Pure Website mode for custom sites where project JSON is not available.
- Dedicated cyoap_vue detection and backup mode.
- Offline viewer injection for supported viewer packages.

### Asset detection

The downloader scans for:

- Choice images.
- Background images.
- Row and object background images.
- Icons, avatars, portraits, thumbnails, and cover images.
- Markdown image links.
- HTML image tags inside JSON fields.
- Relative and absolute asset paths.
- Audio files, background music, and sound effects.
- CSS referenced images and fonts.
- JavaScript referenced static assets during website mode and deep scan.

### Network and recovery helpers

- Proxy support.
- BebasDNS DNS-over-HTTPS presets.
- Custom DNS-over-HTTPS endpoint support.
- HTTP/2 deep-scan support through `httpx[h2]`.
- Cloudflare modes: `off`, `auto`, `cloudscraper`, and `flaresolverr`.
- Optional `gallery-dl` fallback for supported gallery or post URLs.
- Optional AI Assist for difficult project detection and recovery.

### GUI features

- Dark and light theme.
- Indonesian and English UI toggle.
- Queue and batch support.
- Download history.
- Bandwidth limit.
- Local preview server.
- Feature guide panel.
- CYOA Manager integration.
- Offline Viewer Manager.
- Cloudflare and FlareSolverr configuration panel.
- AI Assist configuration panel.

## Supported output modes

| Mode | Best for | Output |
|---|---|---|
| Embedded JSON | Single-file backup | `.json` with base64 images |
| ZIP | Project data plus asset folder | `.zip` with `project.json` and assets |
| Both | Maximum project backup | Embedded JSON plus ZIP |
| Website ZIP | Full website backup as archive | `.zip` |
| Website Folder | Most reliable offline playback | Folder with site files and assets |
| Pure Website ZIP | Custom sites without project JSON | `.zip` |
| Pure Website Folder | Custom sites for local preview | Folder |
| cyoap_vue ZIP | cyoap_vue project backup | `.zip` |
| cyoap_vue Folder | cyoap_vue project preview | Folder |

Recommended mode for most users:

```text
Website Folder
```

Use the local preview server if the project does not work through `file://`.

## Installation

### Requirements

- Python 3.9 or newer.
- Windows 10 or newer, macOS, or Linux.
- Internet connection for downloading project files and assets.

Tested mainly with Python 3.10 to 3.12.

### Clone the repository

```bash
git clone https://github.com/Halo1211/CYOA-Downloader.git
cd CYOA-Downloader
```

### Create a virtual environment

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Windows CMD:

```bat
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### Install dependencies

```bash
pip install -r requirements.txt
```

If you only want the minimal GUI and normal downloader, install the core packages manually:

```bash
pip install requests beautifulsoup4 customtkinter tldextract Pillow json5
```

## Optional system dependencies

### FFmpeg

Required for some `yt-dlp` audio workflows.

Windows:

```powershell
winget install ffmpeg
```

macOS:

```bash
brew install ffmpeg
```

Ubuntu or Debian:

```bash
sudo apt install ffmpeg
```

### unrar

Required only for RAR based offline viewer packages.

macOS:

```bash
brew install unrar
```

Ubuntu or Debian:

```bash
sudo apt install unrar
```

Windows users can install WinRAR or another `unrar` compatible tool.

### FlareSolverr

Required only if you want browser based Cloudflare solving.

Docker:

```bash
docker run -d --name=flaresolverr -p 8191:8191 -e LOG_LEVEL=info --restart unless-stopped ghcr.io/flaresolverr/flaresolverr:latest
```

Windows without Docker:

1. Download the Windows x64 release from the FlareSolverr releases page.
2. Extract it to a folder such as `C:\Tools\FlareSolverr`.
3. Run `flaresolverr.exe`.
4. Use this API endpoint in the app:

```text
http://localhost:8191/v1
```

### Ollama

Required only if you want local AI Assist.

Install Ollama, then pull a model:

```bash
ollama pull llama3.1
```

Default Ollama URL:

```text
http://localhost:11434
```

## Quick start

### Start the GUI

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py
```

The GUI opens automatically when you run the script without arguments.

### Basic GUI workflow

1. Paste the CYOA URL.
2. Choose the output folder.
3. Choose a mode.
4. Use `Website Folder` for the safest offline playback.
5. Click `Download`.
6. Use `Serve` if the output does not work through `file://`.

## CLI usage

### Download with default embedded JSON output

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads
```

### Website Folder output

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads --website-folder
```

### Website ZIP output

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads --website
```

### ZIP output

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads --zip
```

### Both embedded JSON and ZIP

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads --both
```

### Pure Website Folder

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/custom-cyoa/" --output ./downloads --pure-website-folder
```

### cyoap_vue Folder

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads --cyoap-vue-folder
```

### Serve the result after download

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --output ./downloads --website-folder --serve
```

`--serve-port 0` auto-picks a fresh port. This is the default.

## CLI option overview

Common options:

```text
-u, --url URL                      CYOA URL
-o, --output DIR                   Output directory
-L, --list FILE_OR_URL             Batch input source
-z, --zip                          ZIP mode
-b, --both                         Embedded JSON plus ZIP
-W, --website                      Website ZIP
--website-folder                   Website Folder
--pure-website                     Pure Website ZIP
--pure-website-folder              Pure Website Folder
--cyoap-vue                        Probe cyoap_vue before standard detection
--cyoap-vue-website                Force cyoap_vue ZIP
--cyoap-vue-folder                 Force cyoap_vue Folder
-f, --fonts                        Download and localize fonts
-a, --analyse-fonts                Font analysis only
-t, --threads N                    Parallel workers
-w, --wait-time N                  Wait time for browser fallback
--serve                            Start local preview server
--serve-port N                     Local server port. 0 means auto
--language id|en                   UI or CLI language preference
```

Network options:

```text
--proxy URL                        Manual proxy URL
--proxy-mode inherit_env|manual|disabled
--dns IP_OR_DOH_URL                Custom DNS or DoH resolver
--bebasdns default|security|unfiltered|family
--http2 / --no-http2               Enable or disable HTTP/2 deep scan
--bandwidth KBPS                   Bandwidth limit. 0 means unlimited
```

Cloudflare options:

```text
--cloudflare off|auto|cloudscraper|flaresolverr
--cf-bypass                        Legacy alias for cloudscraper
--flaresolverr-url URL             Default: http://localhost:8191/v1
--flaresolverr-session temporary|reuse-domain|manual
--flaresolverr-timeout SECONDS
--flaresolverr-wait SECONDS
--flaresolverr-proxy inherit|none
--flaresolverr-test
```

AI options:

```text
--ai-provider anthropic|openai|gemini|ollama
--ai-key KEY                       Per-run key only. Not saved
--ai-key-storage session|env|keyring|plain
--ai-model MODEL
--ollama-url URL
--ai-mode off|diagnostics|auto_fallback|aggressive_recovery
--ai-max-calls N
--ai-max-html-chars N
--ai-max-js-chars N
--ai-clear-key
```

gallery-dl options:

```text
--gallery-dl off|smart|force
--gallery-dl-path gallery-dl
--gallery-dl-config path/to/config.json
```

## Batch download

The GUI and CLI support batch input from TXT, CSV, XLSX, XLS, remote CSV, or Google Sheets CSV export links.

### TXT format

```text
https://example.com/cyoa-one/
https://example.com/cyoa-two/ | Custom Name
https://example.com/cyoa-three/ | Custom Name | website_folder
```

### CSV format

```csv
url,filename,mode
https://example.com/cyoa-one/,CYOA One,website_folder
https://example.com/cyoa-two/,CYOA Two,zip
```

Supported columns:

| Column | Required | Purpose |
|---|---:|---|
| `url`, `link`, `urls`, `links` | Yes | CYOA URL |
| `filename`, `name`, `output`, `title`, `file` | No | Output name |
| `mode`, `output_mode`, `type` | No | Per-row output mode |

## Cloudflare and FlareSolverr

Cloudflare mode choices:

| Mode | Behavior |
|---|---|
| `off` | No Cloudflare helper |
| `auto` | Normal request first, then cloudscraper, then FlareSolverr if configured |
| `cloudscraper` | Force cloudscraper path when available |
| `flaresolverr` | Force FlareSolverr path |

Recommended setting:

```text
Cloudflare Mode: auto
```

FlareSolverr should be used as a fallback, not for every asset. It runs a browser and can use significant memory.

Test FlareSolverr:

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --cloudflare flaresolverr --flaresolverr-test
```

## BebasDNS and DNS options

BebasDNS presets:

```bash
--bebasdns default
--bebasdns security
--bebasdns unfiltered
--bebasdns family
```

Custom DoH resolver:

```bash
--dns "https://security.dns.bebasid.com/dns-query"
```

Plain DNS IP:

```bash
--dns 1.1.1.1
```

DNS changes are process-local. The program does not edit system DNS, router DNS, browser DNS, or hosts files.

## HTTP/2 support

HTTP/2 deep-scan support requires:

```bash
pip install "httpx[h2]"
```

Enable it:

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --website-folder --http2
```

Use `--no-http2` to disable it for a run.

## AI Assist

AI Assist is optional. It helps diagnose difficult projects when normal detection cannot find project data or hidden assets.

Supported providers:

| Provider | Default key source | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | Claude models |
| OpenAI | `OPENAI_API_KEY` | GPT models |
| Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Gemini models |
| Ollama | No API key | Local models |

AI modes:

| Mode | Behavior |
|---|---|
| `off` | AI disabled |
| `diagnostics` | Analyze only, no recovery changes |
| `auto_fallback` | Use AI when normal detection fails |
| `aggressive_recovery` | Also use AI for JS or deep-scan asset discovery |

API key storage options:

| Storage | Security | Behavior |
|---|---|---|
| `session` | Best default | Key stays in memory only |
| `env` | Good | Reads provider environment variable |
| `keyring` | Best persistent | Uses OS Credential Manager through `keyring` |
| `plain` | Not recommended | Stores key in settings as plain text |

Examples:

```bash
# Anthropic through environment variable
setx ANTHROPIC_API_KEY "sk-ant-..."
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --ai-provider anthropic --ai-key-storage env --ai-mode auto_fallback
```

```bash
# OpenAI through environment variable
setx OPENAI_API_KEY "sk-..."
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --ai-provider openai --ai-key-storage env --ai-mode auto_fallback
```

```bash
# Ollama local AI, no key required
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --ai-provider ollama --ollama-url http://localhost:11434 --ai-mode auto_fallback
```

## gallery-dl fallback

`gallery-dl` is optional. It should not be treated as a universal image downloader.

Recommended mode:

```text
gallery-dl: off
```

Use `smart` only when the source URL is a supported post or gallery URL:

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --gallery-dl smart
```

Use `force` only for advanced debugging.

## Local preview server

Some CYOAs do not work correctly when opened through `file://`. Use the local preview server instead.

The server:

- Uses a fresh port by default.
- Serves the selected output folder explicitly.
- Sends no-cache headers.
- Opens a cache-clearing route first.
- Clears browser storage when possible.
- Redirects to the fresh preview URL with a cache-busting query string.

CLI:

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --website-folder --serve
```

## Reports and logs

| File | Purpose |
|---|---|
| `cyoa_downloader.log` | Main technical log |
| `backup_report.txt` | Website or cyoap backup summary |
| `failed_assets.txt` | Failed asset list for non-website outputs |
| `failed_images.txt` | Failed image list used by retry image workflows |
| `failed_urls.txt` | Failed batch URL list |
| `download_history.json` | Local history file in the user config folder |

`broken_assets_report.html` is no longer generated.

## Troubleshooting

### The CYOA opens but shows an old project

Use the built-in `Serve` function instead of opening the HTML file directly. Close old preview tabs, then serve again. If needed, test in an incognito window.

### Images are missing

Try:

1. Use `Website Folder` mode.
2. Enable fonts if needed with `--fonts`.
3. Check `backup_report.txt` or `failed_assets.txt`.
4. Try `--cloudflare auto` if the site is protected.
5. Try `--gallery-dl smart` only if the missing asset comes from a supported gallery or post URL.

### Cloudflare blocks the download

Install cloudscraper:

```bash
pip install cloudscraper
```

Then run:

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --cloudflare auto
```

For harder challenges, start FlareSolverr and use:

```bash
python cyoa_downloader_v7_3_9_no_broken_report.py --url "https://example.com/cyoa/" --cloudflare auto --flaresolverr-url http://localhost:8191/v1
```

### HTTP/2 does not work

Install HTTP/2 support:

```bash
pip install "httpx[h2]"
```

Then retry with:

```bash
--http2
```

### YouTube or external audio does not download

Install yt-dlp:

```bash
pip install yt-dlp
```

Install FFmpeg using your OS package manager.

### RAR viewer package does not extract

Install rarfile:

```bash
pip install rarfile
```

Install `unrar` or WinRAR at the system level.

### XLSX batch import fails

Install:

```bash
pip install openpyxl pandas
```

### AI Assist does not run

Check:

1. `--ai-mode` is not `off`.
2. Provider is correct.
3. API key storage is correct.
4. Environment variable exists if using `env`.
5. Ollama is running if using local AI.
6. `--ai-max-calls` is not exhausted.

## Recommended repository structure

```text
CYOA-Downloader/
├── README.md
├── requirements.txt
├── cyoa_downloader_v7_3_9_no_broken_report.py
├── CYOA_Downloader_v739_Docs_COMPLETE.html
├── CYOA_Downloader_Handoff_v20_NO_BROKEN_REPORT.md
├── examples/
│   ├── batch_urls_example.txt
│   └── batch_urls_example.csv
└── LICENSE
```

## Known limitations

This tool is useful, but not perfect.

Some projects can still fail because:

- The source blocks automated access.
- Assets require login or cookies.
- Assets are generated dynamically at runtime.
- The viewer is heavily custom.
- JavaScript is obfuscated.
- Remote APIs are required after page load.
- Cloudflare or other bot protection blocks requests.
- Browser service workers or local storage preserve old viewer state.
- Audio sources use platforms that cannot be archived reliably.

Known architectural issue:

- The main download pipeline still uses a global working-directory change protected by a lock. It works for normal usage, but the ideal future refactor is a full absolute-path pipeline.

## Bug report template

When reporting a bug, include:

```text
CYOA Downloader version:
Python version:
Operating system:
Command or GUI mode used:
CYOA URL:
Output mode:
Cloudflare mode:
DNS or BebasDNS mode:
HTTP/2 enabled: yes/no
AI provider and AI mode:
gallery-dl mode:
Relevant log lines:
Expected result:
Actual result:
```

Attach:

- `cyoa_downloader.log`
- `backup_report.txt` if available
- `failed_assets.txt` if available
- Screenshot of the GUI error if relevant

## Ethical use

Use this tool for personal archival, preservation, and offline access.

Respect:

- Original CYOA creators.
- Website terms of service.
- Copyright rules.
- Community sharing rules.
- Private or restricted content.

Do not use this tool to redistribute content without permission.

## License

Choose the license that matches your release goal.

- GPLv3

## Credits

Created for Interactive CYOA preservation, offline playback, and personal archival.

Developed through iterative AI-assisted debugging, testing, and documentation.
