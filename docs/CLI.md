# CLI reference

The command line is intended for repeatable downloads, batch processing, scripted backups, and
diagnostics. Running the program without any arguments launches the GUI instead.

```bash
python cyoa_downloader.py [options] [url] [filename]
```

> On Windows you may use `py -3 cyoa_downloader.py ...`. If you packaged the EXE, replace
> `python cyoa_downloader.py` with the executable name.

> **New to the CLI?** You can ignore most flags. Start with one of these:
> `--icc-folder` for a playable folder, `--icc` for one ZIP, or no mode flag
> for a simple project snapshot. Use the GUI first if you prefer buttons.

## First CLI run

Run these commands from the folder that contains `cyoa_downloader.py`:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py "https://example.com/cyoa/" --icc-folder --output downloads
```

The first command checks your installation. The second command downloads one
CYOA into a new `downloads/` folder. Replace the example URL with the real page
you want to archive.

On Windows, `py -3` can replace `python` if that is the command your Python
installation provides:

```powershell
py -3 cyoa_downloader.py "https://example.com/cyoa/" --icc-folder --output downloads
```

### Which command should I use?

| What you want | Command |
| --- | --- |
| Simplest project snapshot | `python cyoa_downloader.py "URL"` |
| Folder you can inspect and play | `python cyoa_downloader.py "URL" --icc-folder` |
| One ZIP file to store/share | `python cyoa_downloader.py "URL" --icc` |
| Custom viewer when normal detection fails | `python cyoa_downloader.py "URL" --pure-website-folder` |
| Several URLs from a list | `python cyoa_downloader.py --list urls.txt --icc-folder` |

Start with one URL. Once that works, move to batch lists and optional network
or media features.

## Contents

- [Quick examples](#quick-examples)
- [Input and output](#input-and-output)
- [Output modes](#output-modes)
- [Assets, fonts, and performance](#assets-fonts-and-performance)
- [Network: proxy, DNS, Cloudflare, HTTP/2](#network-proxy-dns-cloudflare-http2)
- [AI Assist](#ai-assist)
- [Integrations and serving](#integrations-and-serving)
- [Diagnostics and maintenance](#diagnostics-and-maintenance)
- [Exit codes](#exit-codes)

---

## Quick examples

```bash
# Most complete offline backup (a folder you can open and play):
python cyoa_downloader.py "https://example.com/cyoa/" -o ./downloads --icc-folder

# Single shareable archive:
python cyoa_downloader.py "https://example.com/cyoa/" -o ./downloads --icc

# Batch from a list, all as ICC folders:
python cyoa_downloader.py -L urls.txt -o ./downloads --icc-folder

# Download, then preview through a local server:
python cyoa_downloader.py "https://example.com/cyoa/" --icc-folder --serve --serve-port 0

# Verify a finished backup is intact (no re-download):
python cyoa_downloader.py --verify "./downloads/MyCYOA"
```

---

## Input and output

| Flag | Value | Description |
| --- | --- | --- |
| `url` (positional) | URL | Source CYOA URL. |
| `filename` (positional) | text | Optional output filename stem. |
| `-u`, `--url` | URL | Source URL; overrides the positional URL. |
| `-o`, `--output` | folder | Output directory (created if missing). |
| `-L`, `--list` | path/URL | Batch input: `.txt` / `.csv` / `.xlsx` / `.xls`, or a remote CSV / Google Sheets URL. |
| `--language` | `id` / `en` | CLI/UI language; saved only when explicitly provided. |

Batch file formats are documented in [USER_GUIDE.md](./USER_GUIDE.md).

---

## Output modes

If no mode flag is given, the default embedded-JSON behavior is used.

| Flag | Output | Best for |
| --- | --- | --- |
| (none) | Embedded JSON | Simple project snapshot. |
| `-z`, `--zip` | ZIP (project.json + external assets) | Compact archive with files kept separate. |
| `-b`, `--both` | Embedded JSON **and** ZIP | Maximum compatibility. |
| `--icc` | ICC viewer ZIP | A single shareable offline package. |
| `--icc-folder` | ICC viewer folder | **Most complete offline playback.** |
| `--pure-website` | Site mirror ZIP | Custom sites without standard project data. |
| `--pure-website-folder` | Site mirror folder | Inspecting custom sites. |
| `--cyoap-vue` | Auto-probe CYOAP Vue | Mixed/uncertain CYOAP Vue sources. |
| `--cyoap-vue-website` | CYOAP Vue ZIP | CYOAP Vue archive. |
| `--cyoap-vue-folder` | CYOAP Vue folder | CYOAP Vue offline folder. |

> Legacy keywords `website`, `website_zip`, and `website_folder` remain valid aliases in batch
> files for backward compatibility.

---

## Assets, fonts, and performance

| Flag | Value | Description |
| --- | --- | --- |
| `-f`, `--fonts` | boolean | Download and localize fonts (ZIP/ICC modes). |
| `-a`, `--analyse-fonts` | boolean | Print a font analysis report only. |
| `-t`, `--threads`, `--workers` | integer | Parallel download threads (default: 4). |
| `-w`, `--wait-time`, `--wait` | seconds | Wait after HTTP 429 (default: 60). |
| `--bandwidth` | KB/s | Bandwidth limit; `0` means unlimited. |
| `--no-deep-scan` | boolean | Disable the JS/CSS deep-scan asset pass (on by default). |
| `--no-selenium` | boolean | Disable the headless-browser image fallback (on by default). |
| `--no-ytdlp` | boolean | Disable automatic YouTube audio download. |
| `--gallery-dl` | `off` / `smart` / `force` | Optional gallery/post fallback (default off). |
| `--gallery-dl-path` | path | Custom `gallery-dl` executable. |
| `--gallery-dl-config` | path | Custom `gallery-dl` config JSON. |
| `--itch` | boolean | Enable the optional itch.io asset downloader for itch.io URLs. |
| `--itch-mirror-web` | boolean | Pass `--mirror-web` to itch-dl when supported. |

---

## Network: proxy, DNS, Cloudflare, HTTP/2

| Flag | Value | Description |
| --- | --- | --- |
| `--proxy` | URL | Proxy address, e.g. `http://127.0.0.1:7890`. |
| `--proxy-mode` | `inherit_env` / `manual` / `disabled` | Controls proxy source; `disabled` ignores `HTTP_PROXY`/`HTTPS_PROXY`. |
| `--dns` | IP or DoH URL | Process-local DNS override; empty string restores system DNS. |
| `--bebasdns` | `default` / `security` / `unfiltered` / `family` | BebasDNS DoH resolver variant. |
| `--cloudflare` | `off` / `auto` / `cloudscraper` / `flaresolverr` | Cloudflare handling. Start with `auto`. |
| `--cf-bypass`, `--cloudscraper` | boolean | Legacy alias forcing cloudscraper mode. |
| `--flaresolverr-url` | URL | FlareSolverr API endpoint, e.g. `http://localhost:8191/v1`. |
| `--flaresolverr-session` | `temporary` / `reuse-domain` / `manual` | Session policy; `reuse-domain` keeps cookies per domain. |
| `--flaresolverr-timeout` | seconds | Solve timeout. |
| `--flaresolverr-wait` | seconds | Wait after page load before returning content. |
| `--flaresolverr-proxy` | `inherit` / `none` | Whether FlareSolverr inherits the app proxy. |
| `--flaresolverr-test` | boolean | Test the FlareSolverr endpoint and exit. |
| `--http2` / `--no-http2` | boolean pair | Enable/disable HTTP/2 deep-scan fetches via `httpx`. |
| `--allow-internal-hosts` | boolean | Allow fetching from loopback/RFC1918 hosts. Off by default as an SSRF safeguard; enable only for trusted local setups. |

---

## AI Assist

AI Assist is optional and used only to help locate hidden project data or analyze difficult JS
when deterministic detection struggles.

| Flag | Value | Description |
| --- | --- | --- |
| `--ai-key` | key | API key for this run only (not saved). |
| `--ai-provider` | `anthropic` / `openai` / `gemini` / `ollama` / `deepseek` / `qwen` / `groq` / `openrouter` / `custom` | AI backend. Ollama is local. |
| `--ai-key-storage` | `session` / `env` / `keyring` / `plain` | Where to read/save the key. Prefer `session`, `env`, or `keyring`. |
| `--ai-model` | model name | Override the provider default model. |
| `--ollama-url` | URL | Ollama base URL (default `http://localhost:11434`). |
| `--ai-mode` | `off` / `diagnostics` / `auto_fallback` / `aggressive_recovery` | AI usage level. |
| `--ai-max-calls` | integer | Maximum AI calls per download; `0` is unlimited. |
| `--ai-max-html-chars` | integer | Maximum HTML characters sent to AI per call. |
| `--ai-max-js-chars` | integer | Maximum JS characters sent to AI per call. |
| `--ai-clear-key` | boolean | Clear the stored AI key and exit. |

---

## Integrations and serving

| Flag | Value | Description |
| --- | --- | --- |
| `--cyoa-manager` | boolean | Register the finished `project.json` in CYOA Manager when possible. |
| `--serve` | boolean | Start a local HTTP server for the output after download. |
| `--serve-port` | integer | Serve port; `0` auto-picks a free port. |

---

## Diagnostics and maintenance

| Flag | Value | Description |
| --- | --- | --- |
| `--verify` | FOLDER | Validate a finished output folder (read-only) and exit. |
| `--write-manifest` | boolean | With `--verify`: write a `cyoa_manifest.json` checksum sidecar and exit, enabling later checksum verification. |
| `--dependency-check` | boolean | Print dependency status and exit. |
| `--self-test` | boolean | Run offline internal smoke tests and exit. |
| `--userscript-info` | boolean | Print Serve-only userscript credit/source notes and exit. |
| `--itch-test` | boolean | Test the itch.io backend and connectivity, then exit. |
| `--export-settings` | FILE | Export current settings (secrets redacted) and exit. |
| `--import-settings` | FILE | Merge settings from a prior export (secrets ignored) and exit. |
| `--gui` | boolean | Force GUI launch. |
| `-h`, `--help` | boolean | Print the full argparse help and exit. |

Integrity verification is covered in detail in
[ADVANCED_FEATURES.md](./ADVANCED_FEATURES.md#11-integrity-verification---verify----write-manifest).

---

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success (including a `--verify` that found no problems). |
| `1` | A blocking problem occurred (for example, `--verify` found missing or corrupted files). |

The full, always-current flag list is available from the program itself:

```bash
python cyoa_downloader.py --help
```
