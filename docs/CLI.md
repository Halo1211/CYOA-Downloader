# Complete CLI Reference — CYOA Downloader v1.0 Release

The CLI supports direct one-shot backups, repeatable scripts, batch jobs, network troubleshooting, optional AI fallback, and local preview serving.

```bash
python cyoa_downloader.py [url] [filename] [options]
```

Run without arguments to open the GUI:

```bash
python cyoa_downloader.py
```

---

## 1. Positional arguments and basic flags

| Argument/flag | Meaning |
| --- | --- |
| `url` | Optional positional CYOA/ICC URL. |
| `filename` | Optional positional output filename/name. |
| `-u`, `--url` | Named URL argument; overrides positional URL. |
| `-o`, `--output` | Output directory. Created automatically if missing. |
| `-L`, `--list` | Batch input source: TXT/CSV/XLSX/XLS, remote CSV, or Google Sheets URL. |
| `--gui` | Force GUI. |
| `--language id/en` | Set CLI/UI language preference. |

Examples:

```bash
python cyoa_downloader.py "https://example.com/cyoa"
python cyoa_downloader.py "https://example.com/cyoa" "My Backup"
python cyoa_downloader.py --url "https://example.com/cyoa" -o output
```

---

## 2. Output mode flags

Only one primary output mode may be selected in a single run.

| Flag | Output | Internal behavior |
| --- | --- | --- |
| no mode flag | Embedded JSON | `embed` |
| `-z`, `--zip` | ZIP with external assets | `zip` |
| `-b`, `--both` | Embedded JSON + ZIP | `both` |
| `--icc` | Full offline ICC viewer ZIP | `website_zip` internal key |
| `--icc-folder` | Full offline ICC viewer folder | `website_folder` internal key |
| `--pure-website` | Site mirror ZIP | Skip normal project JSON discovery first. |
| `--pure-website-folder` | Site mirror folder | Folder version of pure website mode. |
| `--cyoap-vue` | Auto-probe CYOAP Vue | Try CYOAP Vue before standard ICC. |
| `--cyoap-vue-website` | CYOAP Vue ZIP | Dedicated `dist/platform.json`/`dist/nodes/list.json` flow. |
| `--cyoap-vue-folder` | CYOAP Vue folder | Folder version of dedicated CYOAP Vue mode. |

Removed v1.0 flags:

| Removed | Use instead |
| --- | --- |
| `--website` | `--icc` |
| `-W` | `--icc` |
| `--website-folder` | `--icc-folder` |

---

## 3. Common download commands

```bash
# Embedded JSON
python cyoa_downloader.py "URL" -o output

# ZIP
python cyoa_downloader.py --zip "URL" -o output

# Embedded JSON + ZIP
python cyoa_downloader.py --both "URL" -o output

# Full offline ICC viewer ZIP
python cyoa_downloader.py --icc "URL" -o output

# Full offline ICC viewer folder
python cyoa_downloader.py --icc-folder "URL" -o output_folder

# Full offline ICC folder + local preview
python cyoa_downloader.py --icc-folder "URL" -o output_folder --serve

# Pure website mirror
python cyoa_downloader.py --pure-website "URL" -o output
python cyoa_downloader.py --pure-website-folder "URL" -o output_folder

# Dedicated CYOAP Vue mode
python cyoa_downloader.py --cyoap-vue-website "URL" -o output
python cyoa_downloader.py --cyoap-vue-folder "URL" -o output_folder
```

---

## 4. Batch mode

### TXT

```text
https://example.com/cyoa/
https://example.com/cyoa2/ | MyFilename
https://example.com/cyoa3/ | MyZip | icc
https://example.com/cyoa4/ | MyFolder | icc_folder
```

Run:

```bash
python cyoa_downloader.py --list batch.txt -o outputs
```

### CSV/XLSX/XLS columns

| Logical column | Accepted names | Required |
| --- | --- | --- |
| URL | `url`, `link`, `urls`, `links` | Yes |
| Filename | `filename`, `name`, `output`, `title`, `file` | No |
| Mode | `mode`, `output_mode`, `type` | No |

Supported mode values:

```text
embed, zip, both,
icc, icc_zip, icc_folder,
website_zip, website_folder,
pure_website, pure_website_zip, pure_website_folder,
cyoap_vue, cyoap_vue_zip, cyoap_vue_folder,
auto
```

Run:

```bash
python cyoa_downloader.py --list batch.csv -o outputs
python cyoa_downloader.py --list batch.xlsx -o outputs
python cyoa_downloader.py --list "https://docs.google.com/spreadsheets/d/..." -o outputs
```

---

## 5. Fonts, assets, and scanner flags

| Flag | Description |
| --- | --- |
| `-f`, `--fonts` | Download and localize fonts in ZIP/ICC modes. |
| `-a`, `--analyse-fonts` | Print font analysis report only when used alone; also enables font analysis output with `--fonts`. |
| `--no-deep-scan` | Disable JS/CSS/HTML deep scan. Default is enabled. |
| `--no-selenium` | Disable Playwright/Selenium headless browser fallback. Default is enabled when available. |
| `--no-ytdlp` | Disable automatic YouTube/SoundCloud audio download through `yt-dlp`. |
| `--gallery-dl off/smart/force` | Optional gallery/post extraction fallback. |
| `--gallery-dl-path` | Path to `gallery-dl` executable. |
| `--gallery-dl-config` | Optional `gallery-dl` config JSON path. |

Examples:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --fonts
python cyoa_downloader.py --icc-folder "URL" -o output --no-deep-scan
python cyoa_downloader.py --icc-folder "URL" -o output --gallery-dl smart
```

---

## 6. Performance and stability flags

| Flag | Default | Description |
| --- | --- | --- |
| `-t`, `--threads`, `--workers` | `4` | Parallel download worker count. |
| `-w`, `--wait-time`, `--wait` | `60` | Seconds to wait after HTTP 429. |
| `--bandwidth` | `0` | Bandwidth limit in KB/s; `0` means unlimited. |
| `--http2`, `--no-http2` | saved/default | Enable/disable optional HTTP/2 via `httpx[h2]`. |

Recommended unstable-network command:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --threads 2 --wait-time 120 --no-http2
```

---

## 7. Proxy, DNS, and Cloudflare flags

| Flag | Values | Description |
| --- | --- | --- |
| `--proxy` | proxy URL | Example: `http://127.0.0.1:7890`. |
| `--proxy-mode` | `inherit_env`, `manual`, `disabled` | Controls proxy source. |
| `--dns` | DNS IP or DoH URL | Override resolver for process. |
| `--bebasdns` | `default`, `security`, `unfiltered`, `family` | BebasDNS DoH preset. |
| `--cloudflare` | `off`, `auto`, `cloudscraper`, `flaresolverr` | Cloudflare challenge handling. |
| `--cf-bypass`, `--cloudscraper` | boolean | Legacy alias to force cloudscraper. |
| `--flaresolverr-url` | URL | FlareSolverr API endpoint. |
| `--flaresolverr-session` | `temporary`, `reuse-domain`, `manual` | Session/cookie policy. |
| `--flaresolverr-timeout` | seconds | Solve timeout. |
| `--flaresolverr-wait` | seconds | Wait after page load. |
| `--flaresolverr-proxy` | `inherit`, `none` | Whether solver inherits app proxy. |
| `--flaresolverr-test` | none | Test configured FlareSolverr API and exit. |

Examples:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare auto
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare cloudscraper
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare flaresolverr --flaresolverr-url http://localhost:8191/v1
python cyoa_downloader.py --flaresolverr-test
python cyoa_downloader.py --icc-folder "URL" -o output --proxy http://127.0.0.1:7890 --proxy-mode manual
python cyoa_downloader.py --icc-folder "URL" -o output --dns 1.1.1.1
python cyoa_downloader.py --icc-folder "URL" -o output --bebasdns unfiltered
```

---

## 8. AI Assist flags

| Flag | Values | Description |
| --- | --- | --- |
| `--ai-key` | string | Run-only API key; not saved. |
| `--ai-provider` | `anthropic`, `openai`, `gemini`, `ollama`, `deepseek`, `qwen`, `groq`, `openrouter`, `custom` | Provider. |
| `--ai-key-storage` | `session`, `env`, `keyring`, `plain` | Where to read/save the key. |
| `--ai-model` | model name | Override provider default model. |
| `--ollama-url` | URL | Ollama base URL. |
| `--ai-mode` | `off`, `diagnostics`, `auto_fallback`, `aggressive_recovery` | AI behavior mode. |
| `--ai-max-calls` | integer | Maximum AI calls per download; `0` means unlimited. |
| `--ai-max-html-chars` | integer | Maximum HTML characters sent per call. |
| `--ai-max-js-chars` | integer | Maximum JS characters sent per call. |
| `--ai-clear-key` | none | Clear key from plain settings/keyring and exit. |

Examples:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider openai \
  --ai-mode auto_fallback \
  --ai-key "YOUR_KEY"

python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider ollama \
  --ai-model llama3.1 \
  --ai-mode diagnostics
```

---

## 9. Local preview and userscript information

| Flag | Description |
| --- | --- |
| `--serve` | Start local HTTP server for output directory after download. |
| `--serve-port` | Local server port. `0` means auto-pick. |
| `--userscript-info` | Print bundled Serve-only helper credit/source report and exit. |

Recommended:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder --serve
```

---

## 10. CYOA Manager and itch.io flags

| Flag | Description |
| --- | --- |
| `--cyoa-manager` | Register finished `project.json` in CYOA Manager when possible. |
| `--itch` | Enable optional itch.io asset downloader. |
| `--itch-test` | Test itch backend and connectivity, then exit. |
| `--itch-mirror-web` | Pass mirror-web behavior to itch backend when supported. |

---

## 11. Diagnostics and maintenance flags

| Flag | Description |
| --- | --- |
| `--dependency-check` | Print required/optional dependency status and exit. |
| `--self-test` | Run offline internal smoke tests and exit. |
| `--userscript-info` | Print userscript integration report and exit. |
| `--export-settings FILE` | Export settings with secrets redacted and exit. |
| `--import-settings FILE` | Import prior safe settings export and exit. |

Examples:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --userscript-info
python cyoa_downloader.py --export-settings settings.redacted.json
python cyoa_downloader.py --import-settings settings.redacted.json
```

---

## 12. Exit behavior

- `argparse` errors exit with code `2`.
- `--self-test` exits with code `1` if internal smoke tests fail.
- Failed output-folder write checks exit with code `2`.
- Batch mode continues through recoverable per-URL errors and writes `failed_urls.txt`.
