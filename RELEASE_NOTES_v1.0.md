# Release Notes — CYOA Downloader v1.0 Release

CYOA Downloader v1.0 is the first GitHub-oriented stable release package. It focuses on stabilization, ICC terminology, complete documentation, MIT licensing, and release-ready project structure.

---

## Highlights

- Version branding updated to **v1.0 Release**.
- License set to **MIT**.
- GitHub-ready repository structure added.
- Dark/light compatible logo assets added.
- README expanded with full feature documentation.
- Detailed docs added for CLI, GUI, features, usage, installation, troubleshooting, EXE build, and internal workflow.
- User-facing **Website Mode** renamed to **ICC Mode**.
- New CLI flags:
  - `--icc`
  - `--icc-folder`
- Removed old CLI flags for consistency:
  - `--website`
  - `-W`
  - `--website-folder`
- Internal compatibility retained for:
  - `website_zip`
  - `website_folder`

---

## Major feature areas documented

- GUI and CLI workflows.
- Embedded JSON, ZIP, Both, ICC ZIP, ICC Folder, Pure Website, and CYOAP Vue modes.
- Batch import from TXT, CSV, XLSX/XLS, remote CSV, and Google Sheets.
- Deep asset scanning across JSON, HTML, CSS, JavaScript, chunks, manifests, and viewer configs.
- Image/audio/video/font detection.
- Google Fonts and direct font localization.
- Optional `yt-dlp` audio recovery.
- Optional `gallery-dl` fallback.
- Optional Playwright/Selenium headless browser fallback.
- Optional Cloudflare handling with cloudscraper and FlareSolverr.
- Proxy, DNS, BebasDNS, HTTP/2, bandwidth, worker, and rate-limit controls.
- Optional AI Assist with Anthropic, OpenAI, Gemini, Ollama, DeepSeek, Qwen, Groq, OpenRouter, and custom OpenAI-compatible providers.
- Local preview server and Serve Tools.
- Bundled IntCyoaEnhancer-compatible localhost/offline helper with formal credit.
- CYOA Manager integration.
- Optional itch.io backend support.
- Diagnostics/maintenance commands.
- Safety and stabilization features.

---

## Migration guide

Old commands:

```bash
python cyoa_downloader.py --website "URL" -o output
python cyoa_downloader.py -W "URL" -o output
python cyoa_downloader.py --website-folder "URL" -o output_folder
```

New commands:

```bash
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

Batch files may continue using `website_zip` and `website_folder` for compatibility.

---

## Recommended release assets

Upload these to GitHub Release:

```text
Source code.zip
Source code.tar.gz
cyoa-downloader-v1.0-release.zip
CYOA-Downloader-v1.0-Windows-x64.zip  # when EXE build is ready
```

---

## Recommended post-release testing

```bash
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python -m pytest -q
```

Manual checks:

- GUI opens with logo.
- `--icc` maps to ICC ZIP behavior.
- `--icc-folder` maps to ICC Folder behavior.
- removed `--website` flags fail clearly through argparse.
- old batch values `website_zip` and `website_folder` still work.
- ICC folder output contains expected HTML/assets/reports.
- local preview works when using `--serve`.
