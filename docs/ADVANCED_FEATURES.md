# Advanced Features

This guide covers optional and advanced workflows: AI Assist, Cloudflare recovery, proxy/DNS/HTTP2 behavior, media extraction, browser fallback, theme/logo customization, local serving, userscript helper behavior, and CYOA Manager integration.

Start with [Getting Started](./GETTING_STARTED.md) and [User Guide](./USER_GUIDE.md) before using these options. Advanced flags should be added one at a time so failures remain diagnosable.

> **Beginner warning:** these features are optional recovery tools. A normal
> image/viewer backup does not require an AI key, Cloudflare solver, proxy,
> browser automation, or FFMPEG. Only use the section that matches the error
> you are trying to fix.

Optional Python packages are grouped in `requirements-optional.txt`:

```bash
pip install -r requirements-optional.txt
```

You still need to install **FFMPEG separately** through your operating system
for media conversion. Playwright also needs a browser install:

```bash
python -m playwright install chromium
```

---

## 1. Advanced feature rule

Do not enable every recovery option at once.

Recommended escalation:

1. Confirm the URL works in a browser.
2. Run a basic CLI backup.
3. Try `--icc-folder`.
4. Lower request pressure with `--workers 2 --wait 120`.
5. Use Cloudflare/proxy/DNS options only if the network layer is the problem.
6. Use media tools only if media is the problem.
7. Use AI Assist only after deterministic extraction has failed or when diagnostics are useful.

---

## 2. AI Assist

AI Assist is optional. Normal backups do not require AI or an API key.

AI Assist is intended for:

- diagnosing extraction failures;
- helping classify unusual project/viewer structures;
- suggesting recovery strategies;
- fallback analysis after normal parsing fails.

It should not replace deterministic parsing.

### AI modes

| Mode | Behavior | Recommended for |
| --- | --- | --- |
| `off` | No AI calls. | Default and safest mode. |
| `diagnostics` | AI may explain likely extraction problems. | Debugging without aggressive recovery. |
| `auto_fallback` | AI may be used after standard extraction fails. | Difficult projects after normal tools fail. |
| `aggressive_recovery` | More active recovery behavior. | Advanced users only. |

### Providers

Provider presets may include:

- `anthropic`
- `openai`
- `gemini`
- `ollama`
- `deepseek`
- `qwen`
- `groq`
- `openrouter`
- `custom`

A provider preset is only a convenience option. It does not guarantee access, model availability, billing status, or API compatibility.

### Key storage

| Storage | Description | Risk |
| --- | --- | --- |
| `session` | Key exists for this run only. | Safest for temporary use. |
| `env` | Reads from environment variables. | Good for users who manage secrets outside the app. |
| `keyring` | Stores through OS keyring when available. | Best persistent option when supported. |
| `plain` | Stores in local plain settings. | Avoid on shared machines. |

Examples:

```bash
python cyoa_downloader.py "https://example.com/project" --ai-mode diagnostics --ai-provider ollama
```

```bash
python cyoa_downloader.py "https://example.com/project" --ai-mode auto_fallback --ai-provider openai --ai-key "$OPENAI_API_KEY" --ai-max-calls 3
```

Clear saved key:

```bash
python cyoa_downloader.py --ai-clear-key
```

Safety notes:

- Do not paste API keys into public logs or screenshots.
- Use `--ai-max-calls` to control cost.
- Use `--ai-max-html-chars` and `--ai-max-js-chars` to limit data exposure.
- Prefer `diagnostics` before `auto_fallback`.
- Review exported settings before sharing; secret fields should be redacted but manual review is still recommended.

---

## 3. Cloudflare handling

Some sites block normal HTTP clients. The downloader provides recovery modes, but none of them should be treated as guaranteed.

| Mode | Meaning | Notes |
| --- | --- | --- |
| `off` | No Cloudflare recovery. | Best for basic testing. |
| `auto` | Try normal requests, then configured recovery behavior. | Good first recovery mode. |
| `cloudscraper` | Use cloudscraper if installed. | Optional dependency behavior. |
| `flaresolverr` | Use external FlareSolverr service. | Requires separate service. |

Examples:

```bash
python cyoa_downloader.py "https://example.com/project" --cloudflare auto
```

```bash
python cyoa_downloader.py "https://example.com/project" --cloudflare flaresolverr --flaresolverr-url http://localhost:8191/v1
```

Test FlareSolverr:

```bash
python cyoa_downloader.py --flaresolverr-test --flaresolverr-url http://localhost:8191/v1
```

Common FlareSolverr options:

| Option | Purpose |
| --- | --- |
| `--flaresolverr-url` | API endpoint. |
| `--flaresolverr-session` | Session reuse policy. |
| `--flaresolverr-timeout` | Challenge solve timeout. |
| `--flaresolverr-wait` | Extra wait after page load. |
| `--flaresolverr-proxy` | Proxy behavior for the solver. |

Cloudflare behavior changes frequently. A working configuration may fail later if the target site changes.

---

## 4. Proxy, DNS, and HTTP/2

### Proxy

Manual proxy:

```bash
python cyoa_downloader.py "https://example.com/project" --proxy http://127.0.0.1:7890 --proxy-mode manual
```

Ignore environment proxy variables:

```bash
python cyoa_downloader.py "https://example.com/project" --proxy-mode disabled
```

Proxy modes:

| Mode | Meaning |
| --- | --- |
| `inherit_env` | Use environment proxy variables. |
| `manual` | Use the proxy provided through `--proxy`. |
| `disabled` | Ignore proxies. |

### DNS

Use DNS override:

```bash
python cyoa_downloader.py "https://example.com/project" --dns 1.1.1.1
```

Use BebasDNS preset:

```bash
python cyoa_downloader.py "https://example.com/project" --bebasdns unfiltered
```

BebasDNS preset examples may include `default`, `security`, `unfiltered`, and `family`.

### HTTP/2

Enable optional HTTP/2 deep-scan fetching:

```bash
python cyoa_downloader.py "https://example.com/project" --http2
```

Install optional dependency:

```bash
pip install "httpx[http2]"
```

Disable HTTP/2:

```bash
python cyoa_downloader.py "https://example.com/project" --no-http2
```

If HTTP/2 is unavailable, the downloader should fall back to normal request behavior.

---

## 5. Media recovery

Optional media tools:

| Tool | Purpose |
| --- | --- |
| `yt-dlp` | Supported YouTube/SoundCloud/media extraction workflows. |
| FFMPEG | Merge/conversion workflows. |
| `gallery-dl` | Supported gallery/post fallback workflows. |
| Selenium/Playwright | Browser-driven fallback for dynamic content. |

Install optional media tools:

```bash
pip install -U yt-dlp gallery-dl
```

Check FFMPEG:

```bash
ffmpeg -version
```

Disable yt-dlp:

```bash
python cyoa_downloader.py "https://example.com/project" --no-ytdlp
```

Use gallery-dl smart mode:

```bash
python cyoa_downloader.py "https://example.com/project" --gallery-dl smart
```

Media troubleshooting order:

1. Confirm normal project/image backup works.
2. Run `yt-dlp --version`.
3. Run `ffmpeg -version`.
4. Retry with fewer workers.
5. Disable media extraction if it is not needed.
6. Check the report for direct failed media URLs.

---

## 6. Browser fallback

Some assets appear only after JavaScript execution. Browser fallback can help but is heavier than normal HTTP fetching.

Disable Selenium fallback:

```bash
python cyoa_downloader.py "https://example.com/project" --no-selenium
```

Install Playwright browsers if using Playwright-based workflows:

```bash
python -m playwright install chromium
```

Use browser fallback only after normal request-based extraction fails.

---

## 7. Theme and logo behavior

Theme modes:

- `System`
- `Dark`
- `Light`

Default theme is `System`.

Logo files:

```text
assets/logo-light.png
assets/logo-dark.png
assets/logo-source.png
```

Logo replacement guidelines:

- Keep the same filenames for easiest replacement.
- Use transparent PNG when possible.
- Keep contrast acceptable on both dark and light backgrounds.
- Do not change the application version because of asset-only changes.
- Test the GUI after replacing assets.
- Launch from the repository root so the `assets/` folder is discoverable.

GUI visual issues should be reported with screenshots and display scaling information.

---

## 8. Local serve and userscript helper

Local serve exposes downloaded output through localhost. This helps avoid browser restrictions under `file://`.

Example:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --serve --serve-port 0
```

The bundled userscript helper is serve-only. It is intended for local preview/debug workflows and should not modify normal downloaded source files.

Show userscript notes:

```bash
python cyoa_downloader.py --userscript-info
```

---

## 9. CYOA Manager integration

CYOA Manager integration is optional and must remain additive.

Example:

```bash
python cyoa_downloader.py "https://example.com/project" --cyoa-manager
```

Rules:

- Normal downloads must not require CYOA Manager.
- Failure to integrate with CYOA Manager should not destroy the backup.
- Integration behavior should be reported clearly in logs.

---

## 10. Advanced troubleshooting matrix

| Symptom | First action | Advanced action |
| --- | --- | --- |
| Rate limits | `--workers 2 --wait 120` | Add bandwidth limit. |
| Cloudflare challenge | `--cloudflare auto` | FlareSolverr. |
| Proxy interference | `--proxy-mode disabled` | Manual proxy. |
| DNS failure | System DNS first | `--dns` or `--bebasdns`. |
| YouTube/SoundCloud missing | Check `yt-dlp` and FFMPEG | Disable media if not needed. |
| Dynamic assets missing | Keep deep scan enabled | Browser fallback. |
| Project JSON missing | `--icc-folder` | `--pure-website-folder` then Manual Inject. |
| AI calls too many | `--ai-max-calls 3` | Return to `diagnostics` mode. |

---

## 11. Integrity verification (`--verify` / `--write-manifest`)

These are read-only validation tools for an already-downloaded output folder. They never touch the network or change how downloads work.

### Basic check

```bash
python cyoa_downloader.py --verify "path/to/output_folder"
```

Reports: a broken or missing `project.json`, zero-byte asset files, locally-referenced assets (in `project.json`, HTML, CSS, JS) that are missing on disk, and counts from any existing `failed_assets.txt` / `failed_images.txt`. Exit code is `0` when intact and `1` when a blocking problem is found — useful in scripts and CI.

### Checksum verification

For catching files that became **corrupted, truncated, or modified** (not just missing), capture a checksum baseline once, then verify later:

```bash
# 1. Capture baseline (writes cyoa_manifest.json into the folder):
python cyoa_downloader.py --verify "path/to/output_folder" --write-manifest
# 2. Verify any time:
python cyoa_downloader.py --verify "path/to/output_folder"
```

| Aspect | Behavior |
| --- | --- |
| Sidecar file | `cyoa_manifest.json` at the folder root (sha256 + size per file). |
| Opt-in | Written only with `--write-manifest`; never during a normal download. |
| Auto-upgrade | When the sidecar exists, `--verify` automatically does checksum comparison; when absent, it falls back to the reference-resolution checks above. |
| Default path | Unchanged — output folders are identical to before unless you explicitly write a manifest. |

Use this when archiving important backups long-term, or to confirm a copied/moved/cloud-synced backup did not silently corrupt.
