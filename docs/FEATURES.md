# Complete Feature Reference — CYOA Downloader v1.0 Release

This document is the detailed feature inventory for the v1.0 release. It is intentionally more complete than the README so users can understand what the downloader can do before filing issues or requesting support.

---

## 1. Interfaces

| Feature | Entry point | Status | Notes |
| --- | --- | --- | --- |
| Desktop GUI | `python cyoa_downloader.py` | Stable | Auto-launches when no CLI arguments are supplied. |
| Forced GUI | `--gui` | Stable | Opens GUI even if launched from terminal. |
| CLI | `python cyoa_downloader.py [URL] [options]` | Stable | Designed for automation and repeatable runs. |
| Batch mode | `--list FILE_OR_URL` | Stable | TXT, CSV, XLSX/XLS, remote CSV, Google Sheets CSV export. |
| Queue workflow | GUI queue | Stable | Queue multiple URLs before running. |
| Language preference | GUI / `--language id/en` | Stable | Bilingual labels where implemented. |
| Logs | GUI panel, console, log file | Stable | GUI log batching prevents UI spam/freezes. |
| Settings | GUI / CLI flags | Stable | Atomic writes reduce corrupted settings risk. |
| Tests | `python -m pytest` | Basic | Lightweight tests for CLI aliasing, paths, and batch import. |

---

## 2. Output and backup modes

| Mode | CLI flag | Internal destination/behavior | Output | Best use |
| --- | --- | --- | --- | --- |
| Embedded JSON | no mode flag | `embed` | JSON | Simple project data backup. |
| ZIP | `--zip` / `-z` | `zip` | ZIP | Project data plus externalized assets. |
| Both | `--both` / `-b` | `both` | JSON + ZIP | Conservative archival output. |
| ICC ZIP | `--icc` | `website_zip` | ZIP | Full offline ICC viewer package. |
| ICC Folder | `--icc-folder` | `website_folder` | Folder | Best for preview/debug/large assets. |
| Pure Website ZIP | `--pure-website` | pure website ZIP | ZIP | Custom sites where project JSON detection is not wanted first. |
| Pure Website Folder | `--pure-website-folder` | pure website folder | Folder | Inspection/debug mode for custom sites. |
| CYOAP Vue auto | `--cyoap-vue` | auto-probe | depends | Try CYOAP Vue dedicated flow before standard ICC flow. |
| CYOAP Vue ZIP | `--cyoap-vue-website` | CYOAP Vue ZIP | ZIP | Dedicated `dist/platform.json` and `dist/nodes/list.json` flow. |
| CYOAP Vue Folder | `--cyoap-vue-folder` | CYOAP Vue folder | Folder | Inspect or preview CYOAP Vue output. |

### v1.0 ICC terminology

The old user-facing **Website Mode** wording is replaced by **ICC Mode**.

Removed CLI flags:

- `--website`
- `-W`
- `--website-folder`

New CLI flags:

- `--icc` maps to internal `website_zip`
- `--icc-folder` maps to internal `website_folder`

Backward-compatible internal/batch values retained:

- `website_zip`
- `website_folder`

---

## 3. Asset discovery coverage

### 3.1 JSON field scanning

The downloader scans many common ICC/CYOA image and media fields, including:

```text
image, backgroundImage, rowBackgroundImage, objectBackgroundImage,
defaultImage, bgImage, bg, img, thumbnail, coverImage, headerImage,
icon, portrait, avatar, picture, addonBackgroundImage, rowBorderImage,
objectBorderImage, addonBorderImage, backpackBgImage, loadingBgImage,
favicon, negativeImage, selectedImage, unselectedImage, borderImage,
loadingImage, rowImage, choiceImage
```

Audio/media fields include:

```text
audio, audioSrc, backgroundMusic, backgroundAudio, rowAudio,
objectAudio, soundEffect, sfx, bgm, ambience, voiceover, narration,
soundFile, audioFile, musicFile, clickSound, hoverSound, selectSound,
musicUrl, audioUrl, soundUrl
```

Special handling exists for ICC Plus BGM behavior where `bgmId` may be either a direct audio URL or a YouTube video ID depending on the sibling `useAudioURL` field.

### 3.2 Deep scan

Deep scan is enabled by default and can be disabled with:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-deep-scan
```

Deep scan attempts to find assets in:

- linked HTML;
- linked CSS;
- linked JavaScript;
- Vite/Webpack-style chunks;
- manifest-like files;
- CSS `url(...)` references;
- script string literals containing image/audio/video/font paths;
- project aliases and viewer root paths;
- ICC Plus/Svelte viewer configuration references.

### 3.3 Supported asset extensions

| Asset type | Extensions |
| --- | --- |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`, `.svg`, `.avif`, `.ico` |
| Audio | `.mp3`, `.ogg`, `.wav`, `.m4a`, `.aac`, `.flac`, `.opus`, `.weba` |
| Video | `.mp4`, `.webm`, `.ogv`, `.mkv`, `.mov`, `.m4v` |
| Fonts | `.woff`, `.woff2`, `.ttf`, `.otf`, `.eot` |
| Text assets | `.js`, `.mjs`, `.css`, `.html`, `.json` |

### 3.4 Optional external extractor recovery

| Tool | Flag | Purpose |
| --- | --- | --- |
| `yt-dlp` | default on when installed, disable with `--no-ytdlp` | YouTube/SoundCloud audio recovery where legal and supported. |
| `gallery-dl` | `--gallery-dl smart` / `--gallery-dl force` | Gallery/post fallback for supported sites. |
| Playwright/Selenium headless browser | default on when dependency/browser support exists, disable with `--no-selenium` | Rendered-page fallback for image discovery. |
| itch backend | `--itch`, `--itch-test`, `--itch-mirror-web` | Optional itch.io asset download support when local backend/tool is available. |

---

## 4. AI Assist

AI Assist is an optional fallback layer. It does not replace the downloader's deterministic logic. It can help diagnose difficult pages, locate candidate project JSON paths, or propose additional asset URLs from JavaScript snippets.

### 4.1 Providers

Supported provider names:

```text
anthropic, openai, gemini, ollama, deepseek, qwen, groq, openrouter, custom
```

`custom` uses an OpenAI-compatible endpoint configured in settings.

### 4.2 Modes

| Mode | Description |
| --- | --- |
| `off` | Disable AI even if a key exists. |
| `diagnostics` | Use AI for analysis/reporting-style help only. |
| `auto_fallback` | Use AI only when normal detection needs fallback support. |
| `aggressive_recovery` | Allow more aggressive asset recovery suggestions within user-configured budgets. |

### 4.3 AI CLI flags

| Flag | Purpose |
| --- | --- |
| `--ai-key` | Session-only API key for this run; not saved to settings. |
| `--ai-provider` | Provider selection. |
| `--ai-key-storage` | `session`, `env`, `keyring`, or `plain`. |
| `--ai-model` | Explicit model name. |
| `--ollama-url` | Ollama base URL. |
| `--ai-mode` | AI mode. |
| `--ai-max-calls` | Max AI calls per download. `0` means unlimited. |
| `--ai-max-html-chars` | Max HTML characters sent per call. |
| `--ai-max-js-chars` | Max JS characters sent per call. |
| `--ai-clear-key` | Clear configured AI key from plain settings/keyring and exit. |

### 4.4 AI privacy and security notes

- API keys are redacted from logs.
- `--ai-key` is run-only and is not saved to settings.
- `env` and `keyring` storage are preferred over `plain`.
- Use `ollama` for local/offline AI workflows.
- Avoid sending private or sensitive downloaded content to cloud AI providers unless you accept the provider's policies.

---

## 5. Cloudflare, network, proxy, DNS, and HTTP/2

| Feature | Flag | Details |
| --- | --- | --- |
| Cloudflare handling | `--cloudflare off/auto/cloudscraper/flaresolverr` | Configurable challenge handling. |
| cloudscraper alias | `--cf-bypass`, `--cloudscraper` | Forces cloudscraper mode when installed. |
| FlareSolverr endpoint | `--flaresolverr-url` | Usually `http://localhost:8191/v1`. |
| FlareSolverr session | `--flaresolverr-session temporary/reuse-domain/manual` | Cookie/session policy. |
| FlareSolverr timeout | `--flaresolverr-timeout` | Solve timeout in seconds. |
| FlareSolverr wait | `--flaresolverr-wait` | Wait after page load before return. |
| FlareSolverr proxy | `--flaresolverr-proxy inherit/none` | Whether solver inherits app proxy. |
| FlareSolverr test | `--flaresolverr-test` | Test solver API and exit. |
| Manual proxy | `--proxy` | Example: `http://127.0.0.1:7890`. |
| Proxy mode | `--proxy-mode inherit_env/manual/disabled` | Control environment/manual/disabled proxy behavior. |
| DNS override | `--dns` | DNS IP or DoH URL. |
| BebasDNS preset | `--bebasdns default/security/unfiltered/family` | DoH variant shortcut. |
| HTTP/2 | `--http2` / `--no-http2` | Optional `httpx[h2]` support for deep-scan fetches. |
| Worker count | `--threads`, `--workers` | Parallel download workers. |
| 429 wait | `--wait-time`, `--wait` | Delay after rate-limit response. |
| Bandwidth limit | `--bandwidth` | KB/s; `0` is unlimited. |

---

## 6. Local preview, Serve Tools, and bundled userscript helper

| Feature | Description |
| --- | --- |
| Local preview | `--serve` starts a localhost server after output generation. |
| Serve port | `--serve-port`; `0` auto-picks. |
| No-cache serving | Preview server sends no-cache headers for easier testing. |
| Serve Tools overlay | Provides local-only controls for downloaded/offline CYOAs. |
| Bundled helper route | Serves `/__userscripts__/intcyoaenhancer.user.js` locally. |
| Native Cheat Panel | Localhost-only helper panel for offline testing/QoL. |
| Export localStorage | Browser-side helper export. |
| Export IndexedDB | Browser-side helper export. |
| Clear preview storage | Helper route/tool can clear local preview cache/state. |
| Preview token | Fresh token per preview session to reduce stale-tab issues. |

Important: these helpers are for localhost/offline diagnostics, accessibility checks, and quality-of-life testing only.

---

## 7. CYOA Manager and viewer integrations

| Feature | Details |
| --- | --- |
| CYOA Manager registration | `--cyoa-manager` can register completed `project.json` into CYOA Manager's SQLite library when found. |
| CYOA Manager database detection | Searches common OS paths for `library.sqlite3`. |
| Viewer registry | Local viewer manifest support for bundled/custom viewer assets. |
| Viewer archive safety | Archive extraction validates member paths before writing. |
| Viewer overlay | Offline viewer helper logic inspired by CYOA Manager-style overlays. |

---

## 8. Reports, logs, and maintenance commands

| Command/file | Purpose |
| --- | --- |
| `--dependency-check` | Print required and optional dependency status. |
| `--self-test` | Run lightweight offline smoke tests. |
| `--userscript-info` | Print userscript integration credit/source notes. |
| `--export-settings FILE` | Export settings with secrets redacted. |
| `--import-settings FILE` | Import safe settings data; secrets ignored. |
| `cyoa_downloader.log` | Rotating runtime log in output folder. |
| `backup_report.txt` | Downloaded/failed asset details for ICC/site outputs. |
| `failed_assets.txt` | Failed asset report fallback. |
| `failed_urls.txt` | Failed batch URL list. |
| `settings.json.corrupt` | Backup of unreadable settings file. |

---

## 9. Security and stability hardening

| Area | Implementation intent |
| --- | --- |
| URL schemes | Non-HTTP(S) URL handling is guarded in downloader paths. |
| Output paths | URL-derived paths are sanitized and joined under the selected output root. |
| Archive extraction | Archive members are strictly validated to reject traversal/absolute paths. |
| Settings/cache writes | Atomic temp-file + replace pattern. |
| Logging | Secret-looking values are redacted before log output. |
| File logging | Rotating logs and duplicate-handler prevention. |
| GUI queue/logging | Non-blocking log queue with oldest-line eviction on saturation. |
| Concurrent downloads | Global lock protects legacy current-working-directory sections. |
| Large downloads | Worker count, timeout, bandwidth, and failed-asset reporting controls. |
| Missing optional deps | Optional features fail with targeted warnings where possible. |

---

## 10. Known limitations

- v1.0 intentionally removes old Website CLI flags, so external scripts must migrate to `--icc` or `--icc-folder`.
- Some sites block automated requests, rate-limit downloads, or change viewer internals.
- Optional features require optional packages/tools and OS-level support.
- Cloudflare recovery may require a locally running FlareSolverr service.
- Headless browser fallback depends on local browser/driver availability.
- AI Assist is only a helper and may suggest incorrect candidates; deterministic validation still matters.
- Very large projects are easier to debug with `--icc-folder` before ZIP compression.
