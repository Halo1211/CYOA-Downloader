# Credits — CYOA Downloader v1.0 Release

CYOA Downloader is distributed under the MIT License. This document lists third-party integrations, optional tools, and attribution notes.

---

## 1. Project license

- Project: CYOA Downloader
- Release: v1.0 Release
- License: MIT

See [`../LICENSE`](../LICENSE).

---

## 2. IntCyoaEnhancer attribution

The project includes a bundled localhost/offline helper inspired by and compatible with IntCyoaEnhancer-style workflows.

- Name: **IntCyoaEnhancer**
- Author: **agreg**
- License: **MIT**
- Source: **GreasyFork script 438947**
- Source URL: `https://greasyfork.org/en/scripts/438947-intcyoaenhancer`

Important policy:

- CYOA Downloader does not claim ownership of IntCyoaEnhancer.
- The bundled helper is a localhost/offline integration helper for downloaded CYOAs.
- The helper is used for preview, diagnostics, accessibility checks, and quality-of-life testing.
- Downloaded CYOA output files are not treated as original works of this project.

---

## 3. Optional tool integrations

| Tool/integration | Purpose | Notes |
| --- | --- | --- |
| `yt-dlp` | Optional YouTube/SoundCloud media recovery | Used only when installed and not disabled. |
| `gallery-dl` | Optional gallery/post extractor fallback | Controlled by `--gallery-dl`. |
| `cloudscraper` | Optional Cloudflare recovery | Controlled by `--cloudflare cloudscraper` or `--cf-bypass`. |
| FlareSolverr | Optional Cloudflare browser solver | External service; configured with `--flaresolverr-url`. |
| Selenium | Optional rendered-page fallback | Requires local browser/driver support. |
| CYOA Manager | Optional local library registration | Uses local SQLite library when available. |
| itch backend/tooling | Optional itch.io asset support | Controlled by `--itch`. |
| Ollama | Optional local AI provider | Used through AI Assist when configured. |

---

## 4. Python dependencies

Core and optional packages are listed in `requirements.txt` and documentation. Each package remains under its own license. Users/distributors should review dependency licenses when preparing a binary release.

---

## 5. AI provider note

AI Assist can connect to external AI providers when configured by the user. Supported provider names include Anthropic, OpenAI, Gemini, DeepSeek, Qwen, Groq, OpenRouter, custom OpenAI-compatible endpoints, and local Ollama.

The project does not bundle these services and does not claim affiliation with them.

---

## 6. CYOA content note

CYOA Downloader is a backup utility. It does not grant ownership or redistribution rights over third-party CYOA projects, images, audio, video, fonts, or viewer assets. Users are responsible for respecting author rights, site terms, and applicable law.
