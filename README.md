<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="180">
  </picture>
</p>

<h1 align="center">CYOA Downloader — v1.0 Release</h1>

<p align="center">
  Stable ICC / Interactive CYOA backup utility with GUI, CLI, batch import, ICC offline viewer export, deep asset recovery, AI-assisted fallback, Cloudflare handling, local preview tools, and GitHub-ready release packaging.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg">
  <img alt="Release" src="https://img.shields.io/badge/Release-v1.0-orange.svg">
  <img alt="GUI and CLI" src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-purple.svg">
</p>

---

## Table of contents

- [What is this?](#what-is-this)
- [Quick start](#quick-start)
- [Complete feature overview](#complete-feature-overview)
- [Output modes](#output-modes)
- [ICC Mode terminology](#icc-mode-terminology)
- [CLI examples](#cli-examples)
- [AI Assist](#ai-assist)
- [Deep scan and asset recovery](#deep-scan-and-asset-recovery)
- [Cloudflare, proxy, DNS, HTTP/2](#cloudflare-proxy-dns-http2)
- [Local preview and userscript tools](#local-preview-and-userscript-tools)
- [Batch import](#batch-import)
- [Reports and diagnostics](#reports-and-diagnostics)
- [Documentation map](#documentation-map)
- [Credits and license](#credits-and-license)

---

## What is this?

**CYOA Downloader** creates local backups of Interactive CYOA / ICC projects. Depending on the selected mode, it can save only the project data, save project data plus assets, or build a full offline ICC viewer package containing HTML, CSS, JavaScript, project JSON, images, audio, video, fonts, reports, and preview helpers.

The tool is designed for:

- users who want a reliable local backup of an ICC/CYOA project;
- maintainers who need repeatable CLI downloads;
- archivists who need asset failure reports and log files;
- advanced users who need Cloudflare recovery, proxy/DNS control, deep scanning, gallery extraction, local preview, or AI-assisted fallback.

> **v1.0 terminology note:** user-facing **Website Mode** has been renamed to **ICC Mode**. The old CLI flags `--website`, `-W`, and `--website-folder` were intentionally removed for consistency. Internal mode keys `website_zip` and `website_folder` are still preserved so older CSV/settings/manifest data can remain compatible.

---

## Quick start

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

Run the GUI by launching the script without arguments:

```bash
python cyoa_downloader.py
```

Run a CLI ICC ZIP backup:

```bash
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output
```

Run a CLI ICC folder backup and preview it locally:

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --serve
```

---

## Complete feature overview

### Interface and workflow features

| Feature | GUI | CLI | Details |
| --- | --- | --- | --- |
| Single URL download | Yes | Yes | Download one CYOA/ICC URL. |
| Queue downloads | Yes | Via batch | GUI queue supports multiple items; CLI uses `--list`. |
| Batch import | Yes | Yes | TXT, CSV, XLSX/XLS, remote CSV, Google Sheets CSV export. |
| Logs | Yes | Yes | Console, GUI log panel, rotating `cyoa_downloader.log`. |
| Language preference | Yes | `--language id/en` | Bilingual UI/help text where implemented. |
| Settings/cache | Yes | Yes | Atomic writes and corrupt-settings backup handling. |
| Dependency check | Yes/CLI | `--dependency-check` | Required/optional module status. |
| Self-test | CLI | `--self-test` | Offline smoke checks for internal helpers. |
| Settings export/import | CLI | `--export-settings`, `--import-settings` | Secrets are redacted/ignored. |
| GitHub release readiness | Docs | Docs | README, changelog, credits, install, usage, tests, MIT License. |

### Download and output features

| Feature | Flag/mode | Details |
| --- | --- | --- |
| Embedded JSON | default | Saves a project JSON with embedded/base64 image data where supported. |
| External ZIP | `--zip` | Saves project data and assets as external files inside ZIP. |
| Both | `--both` | Saves embedded JSON and ZIP outputs in one run. |
| ICC ZIP | `--icc` | Full offline ICC viewer package as ZIP. |
| ICC Folder | `--icc-folder` | Full offline ICC viewer package as folder. Recommended for debugging/large outputs. |
| Pure Website ZIP | `--pure-website` | Mirrors viewer HTML/CSS/JS/site assets without normal project JSON discovery first. |
| Pure Website Folder | `--pure-website-folder` | Folder version of Pure Website mode. |
| CYOAP Vue auto-probe | `--cyoap-vue` | Tries dedicated CYOAP Vue flow before standard detection. |
| CYOAP Vue ZIP | `--cyoap-vue-website` | Dedicated backup for projects using `dist/platform.json` and `dist/nodes/list.json`. |
| CYOAP Vue Folder | `--cyoap-vue-folder` | Folder version of dedicated CYOAP Vue mode. |
| Font localization | `--fonts` | Downloads Google Fonts and direct font files when possible. |
| Font analysis only | `--analyse-fonts` | Prints a font analysis report without normal asset download when used alone. |

### Asset detection and recovery features

| Feature | Details |
| --- | --- |
| Project JSON discovery | Attempts to locate ICC/CYOA project data from page content, scripts, known viewer patterns, and fallbacks. |
| Image field scanning | Handles `image`, `backgroundImage`, `rowBackgroundImage`, `objectBackgroundImage`, `defaultImage`, `bgImage`, `thumbnail`, `coverImage`, `headerImage`, `icon`, `portrait`, `avatar`, `selectedImage`, `unselectedImage`, `borderImage`, and related ICC Plus fields. |
| Deep scan | JS/CSS/HTML scanning for additional asset URLs, chunk references, manifests, CSS `url(...)`, and viewer-linked files. Enabled by default. Disable with `--no-deep-scan`. |
| Plugin-style scanner registry | Internal extension point for additional asset scanners and engine detectors. |
| Image formats | PNG, JPG/JPEG, GIF, WebP, BMP, SVG, AVIF, ICO. |
| Audio formats | MP3, OGG, WAV, M4A, AAC, FLAC, OPUS, WEBA. |
| Video formats | MP4, WebM, OGV, MKV, MOV, M4V. |
| YouTube/SoundCloud audio | Optional `yt-dlp` integration; disable with `--no-ytdlp`. |
| gallery-dl fallback | Optional post/gallery extractor fallback through `--gallery-dl smart` or `--gallery-dl force`. |
| Playwright/Selenium headless fallback | Optional browser-based fallback for rendered pages; enabled when dependency/browser support exists. Disable with `--no-selenium`. |
| Failed asset reporting | Writes failed asset details into `backup_report.txt` or `failed_assets.txt`. |
| Batch failed URL report | Failed batch URLs are appended to `failed_urls.txt`. |

### Network, Cloudflare, and recovery features

| Feature | Flag/settings | Details |
| --- | --- | --- |
| Retry session | Built-in | Uses retry-capable HTTP requests for transient errors. |
| 429 wait/backoff | `--wait-time` | Configurable wait time after HTTP 429/rate limit. |
| Thread control | `--threads` / `--workers` | Parallel download worker count. |
| Bandwidth limit | `--bandwidth` | KB/s cap; `0` means unlimited. |
| Proxy | `--proxy`, `--proxy-mode` | Manual proxy, environment proxy, or disabled proxy mode. |
| DNS override | `--dns` | Plain DNS IP or DoH URL. |
| BebasDNS | `--bebasdns` | `default`, `security`, `unfiltered`, or `family` DoH presets. |
| HTTP/2 | `--http2` / `--no-http2` | Optional HTTP/2 via `httpx[h2]` for deep-scan fetches. |
| Cloudflare auto mode | `--cloudflare auto` | Normal request first, then challenge-aware fallback when needed. |
| cloudscraper | `--cloudflare cloudscraper` or `--cf-bypass` | Optional cloudscraper fallback when installed. |
| FlareSolverr | `--cloudflare flaresolverr` | Optional local/browser solver through FlareSolverr. |
| FlareSolverr test | `--flaresolverr-test` | Tests configured FlareSolverr API and exits. |

### AI Assist features

AI Assist is optional and disabled unless configured. It is designed for diagnostics and recovery assistance, not for changing the main download concept.

| Feature | Details |
| --- | --- |
| Providers | Anthropic, OpenAI, Gemini, Ollama, DeepSeek, Qwen, Groq, OpenRouter, and custom OpenAI-compatible endpoints. |
| Modes | `off`, `diagnostics`, `auto_fallback`, `aggressive_recovery`. |
| Project detection assist | Can help identify project JSON candidates from difficult HTML. |
| JS asset scan assist | Can inspect limited JS text to suggest missed asset URLs in aggressive recovery mode. |
| Viewer logic analysis | Can summarize how a viewer appears to load project data for troubleshooting. |
| Budget controls | `--ai-max-calls`, `--ai-max-html-chars`, `--ai-max-js-chars`. |
| Key storage | `session`, `env`, `keyring`, or `plain`; CLI `--ai-key` is session-only for that run. |
| Local model option | Ollama provider can run against local Ollama and does not require a cloud API key. |

Example:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider openai \
  --ai-mode auto_fallback \
  --ai-key "YOUR_KEY"
```

For local Ollama:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider ollama \
  --ai-model llama3.1 \
  --ai-mode diagnostics
```

### Local preview and helper features

| Feature | Details |
| --- | --- |
| Local preview server | `--serve` starts a localhost preview after download. |
| Port control | `--serve-port 0` auto-picks a port; a specific port can be supplied. |
| Serve Tools overlay | Local-only tools for preview/debugging downloaded CYOAs. |
| Bundled IntCyoaEnhancer-compatible helper | Served locally for downloaded/offline CYOAs; source credit retained. |
| Native Cheat Panel | Localhost/offline helper panel for quality-of-life testing. |
| Export localStorage | Preview helper can export localStorage. |
| Export IndexedDB | Preview helper can export IndexedDB data. |
| Clear preview storage | Preview helper route/tool can clear local preview state. |
| Preview session token | Protects against stale browser tabs re-opening old preview sessions. |

> Serve tools and bundled helper scripts are intended for localhost/offline debugging, accessibility checks, and quality-of-life testing only. They are not used to claim ownership of third-party userscripts or external projects.

### Integration features

| Integration | Details |
| --- | --- |
| CYOA Manager | `--cyoa-manager` can register a completed `project.json` in CYOA Manager's SQLite library when available. |
| Viewers registry | Local viewer manifest support for bundled/custom viewers. |
| IntCyoaEnhancer helper | Bundled localhost/offline helper with formal credit to IntCyoaEnhancer by agreg, MIT License, GreasyFork script 438947. |
| itch.io backend | Optional `--itch`, `--itch-test`, and `--itch-mirror-web` support when local itch backend/tooling is available. |
| External extractors | Optional `yt-dlp` and `gallery-dl` integrations. |

### Safety and stability features

| Area | Protection |
| --- | --- |
| Path handling | Sanitized relative paths and safe joins for URL-derived files. |
| Archive handling | Strict archive member validation to reduce traversal risk. |
| Settings/cache | Atomic writes via temporary files and replacement. |
| Corrupt settings | Corrupt settings are backed up before defaults are used. |
| Logging | Sensitive token/password/cookie/bearer-looking values are redacted. |
| File logs | Rotating log handler with duplicate-handler guard. |
| GUI stability | Batched log flush and non-blocking GUI log queue. |
| Concurrency | Worker-count control plus global download lock around legacy `os.chdir()` paths. |
| Partial failures | Failed assets are reported instead of crashing the whole app when recoverable. |
| Missing dependencies | Optional dependency absence should produce feature-specific warnings instead of crashing core workflows. |

---

## Output modes

| Mode | CLI flag | Output | Recommended use |
| --- | --- | --- | --- |
| Embedded JSON | no mode flag | JSON | Small/medium project backup. |
| ZIP | `--zip` | ZIP | Project plus external assets. |
| Both | `--both` | JSON + ZIP | Conservative archival backup. |
| ICC ZIP | `--icc` | ZIP | Shareable offline ICC viewer. |
| ICC Folder | `--icc-folder` | Folder | Debugging, preview, large downloads, EXE users. |
| Pure Website ZIP | `--pure-website` | ZIP | Custom viewer/site mirror. |
| Pure Website Folder | `--pure-website-folder` | Folder | Custom viewer inspection. |
| CYOAP Vue ZIP | `--cyoap-vue-website` | ZIP | CYOAP Vue projects. |
| CYOAP Vue Folder | `--cyoap-vue-folder` | Folder | CYOAP Vue inspection. |

---

## ICC Mode terminology

Use these v1.0 flags:

```bash
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

Removed for consistency:

| Removed flag | Replacement |
| --- | --- |
| `--website` | `--icc` |
| `-W` | `--icc` |
| `--website-folder` | `--icc-folder` |

Compatibility that remains:

| Compatibility item | Status |
| --- | --- |
| Batch mode value `website_zip` | Still accepted. |
| Batch mode value `website_folder` | Still accepted. |
| Settings/manifest internal key `website_zip` | Preserved. |
| Settings/manifest internal key `website_folder` | Preserved. |
| Old CLI flag `--website` | Removed intentionally. |
| Old CLI flag `-W` | Removed intentionally. |
| Old CLI flag `--website-folder` | Removed intentionally. |

---

## CLI examples

### Basic outputs

```bash
python cyoa_downloader.py "https://example.com/cyoa" -o output
python cyoa_downloader.py --zip "https://example.com/cyoa" -o output
python cyoa_downloader.py --both "https://example.com/cyoa" -o output
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder
```

### Deep scan and recovery tuning

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --threads 2 --wait-time 120
python cyoa_downloader.py --icc-folder "URL" -o output --no-deep-scan
python cyoa_downloader.py --icc-folder "URL" -o output --no-selenium
python cyoa_downloader.py --icc-folder "URL" -o output --gallery-dl smart
```

### Cloudflare / FlareSolverr

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare auto
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare cloudscraper
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare flaresolverr --flaresolverr-url http://localhost:8191/v1
python cyoa_downloader.py --flaresolverr-test
```

### Proxy, DNS, and HTTP/2

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --proxy http://127.0.0.1:7890 --proxy-mode manual
python cyoa_downloader.py --icc-folder "URL" -o output --proxy-mode disabled
python cyoa_downloader.py --icc-folder "URL" -o output --dns 1.1.1.1
python cyoa_downloader.py --icc-folder "URL" -o output --bebasdns unfiltered
python cyoa_downloader.py --icc-folder "URL" -o output --http2
```

### Diagnostics and maintenance

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --userscript-info
python cyoa_downloader.py --export-settings settings.redacted.json
python cyoa_downloader.py --import-settings settings.redacted.json
```

---

## AI Assist

AI Assist can help with difficult cases where normal heuristics do not find project data or deep-scan assets. It is optional and should be treated as a fallback/diagnostic layer.

Supported providers:

```text
anthropic, openai, gemini, ollama, deepseek, qwen, groq, openrouter, custom
```

Supported modes:

| Mode | Behavior |
| --- | --- |
| `off` | AI disabled. |
| `diagnostics` | Analysis/reporting help only. |
| `auto_fallback` | Uses AI only after normal methods need help. |
| `aggressive_recovery` | Allows more aggressive JS/asset candidate recovery within configured budgets. |

Important privacy note: only limited HTML/JS snippets are sent according to configured budgets. Do not use cloud AI providers for private material unless you are comfortable with the provider's policy. Use `ollama` for local-only AI workflows.

---

## Deep scan and asset recovery

Deep scan is enabled by default. It scans HTML, CSS, JavaScript, manifests, and linked files for additional assets that may not appear directly in the main project JSON.

Disable it only when troubleshooting or when a site behaves poorly:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-deep-scan
```

Recommended recovery order for missing assets:

1. Run `--icc-folder` instead of `--icc` so files can be inspected before compression.
2. Use fewer workers: `--threads 2`.
3. Increase rate-limit wait: `--wait-time 120`.
4. Keep deep scan enabled.
5. Try `--gallery-dl smart` for supported gallery/post URLs.
6. Try Cloudflare auto or FlareSolverr if the page is challenged.
7. Enable AI Assist in `auto_fallback` only when normal detection still fails.

---

## Cloudflare, proxy, DNS, HTTP/2

CYOA Downloader includes optional network controls for difficult sites:

- `--cloudflare auto` attempts normal request flow first, then Cloudflare-aware fallback when challenge signs are detected.
- `--cloudflare cloudscraper` uses the optional `cloudscraper` package.
- `--cloudflare flaresolverr` uses a running FlareSolverr service.
- `--proxy` and `--proxy-mode` control proxy usage.
- `--dns` and `--bebasdns` control resolver behavior.
- `--http2` enables optional HTTP/2 fetching via `httpx[h2]` where supported.

These features are optional. Normal CYOA/ICC backups should work without them when the site is directly reachable.

---

## Local preview and userscript tools

Use `--serve` after folder output to open a local preview:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder --serve
```

The local preview server includes optional Serve Tools for offline/debug usage, including a bundled IntCyoaEnhancer-compatible localhost helper. This bundled helper retains formal credit:

- Name: IntCyoaEnhancer
- Author: agreg
- License: MIT
- Source: GreasyFork script 438947

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

| Column | Required | Aliases |
| --- | --- | --- |
| URL | Yes | `url`, `link`, `urls`, `links` |
| Filename | No | `filename`, `name`, `output`, `title`, `file` |
| Mode | No | `mode`, `output_mode`, `type` |

Supported batch mode values include:

```text
embed, zip, both, icc, icc_zip, icc_folder,
website_zip, website_folder,
pure_website, pure_website_zip, pure_website_folder,
cyoap_vue, cyoap_vue_zip, cyoap_vue_folder
```

Run batch:

```bash
python cyoa_downloader.py --list batch.csv -o outputs
```

---

## Reports and diagnostics

Generated files may include:

| File/report | Purpose |
| --- | --- |
| `cyoa_downloader.log` | Rotating runtime log. |
| `backup_report.txt` | Downloaded and failed asset summary for ICC/site outputs. |
| `failed_assets.txt` | Failed asset details when no backup report is available. |
| `failed_urls.txt` | Batch URL failure list. |
| `settings.json.corrupt` | Backup created when settings cannot be parsed. |
| `settings.redacted.json` | Optional settings export with secrets removed. |

Diagnostic commands:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --userscript-info
```

---

## Documentation map

| File | Purpose |
| --- | --- |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Full feature inventory with detailed matrices. |
| [`docs/CLI.md`](docs/CLI.md) | Full CLI reference and command examples. |
| [`docs/GUI.md`](docs/GUI.md) | GUI workflow, modes, queue, settings, and preview tools. |
| [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) | Internal workflow from URL intake to output reports. |
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | Installation and optional dependencies. |
| [`docs/USAGE.md`](docs/USAGE.md) | Practical workflows. |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Common failures and recovery steps. |
| [`docs/EXE_BUILD.md`](docs/EXE_BUILD.md) | Windows EXE build guidance. |
| [`docs/CREDITS.md`](docs/CREDITS.md) | Credits and third-party integration notes. |
| [`AUDIT_REPORT.md`](AUDIT_REPORT.md) | Audit summary, bug/stability notes, acceptance criteria. |
| [`RELEASE_NOTES_v1.0.md`](RELEASE_NOTES_v1.0.md) | v1.0 release notes and migration guide. |

---

## Credits and license

This project is released under the **MIT License**. See [`LICENSE`](LICENSE).

Bundled localhost/offline helper credit:

- **IntCyoaEnhancer** by **agreg**
- License: **MIT**
- Source: GreasyFork script 438947

The bundled helper is provided only as a localhost/offline integration helper for downloaded CYOAs. This project does not claim authorship of IntCyoaEnhancer or external CYOA projects.

---

## Disclaimer

Use this tool responsibly and respect website terms, author rights, rate limits, and applicable law. Cloudflare, AI Assist, gallery extraction, local preview helper tools, and similar recovery features are intended for legitimate backup, diagnostics, accessibility, and offline testing workflows.
