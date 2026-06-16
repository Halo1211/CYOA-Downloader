# How CYOA Downloader Works — v1.0 Release

This document explains the internal workflow at a practical level. It is useful for maintainers, bug reporters, and users who want to understand why one mode behaves differently from another.

---

## 1. High-level pipeline

```text
Input URL / batch source
        ↓
CLI or GUI validation
        ↓
Network/session configuration
        ↓
Viewer/project detection
        ↓
Project JSON and/or site asset discovery
        ↓
Asset normalization and safe path mapping
        ↓
Parallel download with retries and reports
        ↓
Font/media/deep-scan recovery passes
        ↓
Output folder or ZIP creation
        ↓
Reports/logs/settings update
        ↓
Optional local preview / CYOA Manager registration / itch pass
```

---

## 2. Input handling

The tool accepts:

- one URL;
- URL plus output filename;
- a batch source from TXT/CSV/XLSX/XLS;
- a remote CSV URL;
- a Google Sheets URL that can be converted into a CSV export.

URL validation expects HTTP or HTTPS URLs for network downloads. Batch rows with invalid URLs are ignored or logged as failures depending on context.

---

## 3. Settings and runtime configuration

Settings live under the user's home configuration path and are loaded before runtime options are resolved. v1.0 uses atomic settings writes to reduce the chance of a truncated JSON settings file.

Important settings groups:

- language;
- AI provider/model/key storage/mode/budget;
- proxy/DNS/BebasDNS;
- Cloudflare and FlareSolverr;
- HTTP/2;
- gallery-dl;
- deep scan / Selenium / serve / cheat / itch toggles.

CLI flags override relevant runtime settings for the current run and sometimes persist when explicitly provided.

---

## 4. Mode resolution

The CLI enforces that only one primary output mode is selected.

| User mode | Internal behavior |
| --- | --- |
| default | Embedded JSON. |
| `--zip` | ZIP output. |
| `--both` | Embedded + ZIP output. |
| `--icc` | Internal `website_zip` behavior. |
| `--icc-folder` | Internal `website_folder` behavior. |
| `--pure-website` | Site mirror ZIP without normal project JSON discovery first. |
| `--pure-website-folder` | Site mirror folder. |
| `--cyoap-vue-*` | Dedicated CYOAP Vue flow. |

`website_zip` and `website_folder` are still internal compatibility keys, but the CLI uses ICC terminology.

---

## 5. ICC/project detection

The downloader searches for project data using multiple strategies:

- page HTML analysis;
- viewer-specific patterns;
- linked script/config references;
- project JSON URL candidates;
- ICC Plus/Svelte-related hints;
- fallback detection paths;
- optional AI project detection when enabled and allowed by mode/budget.

When CYOAP Vue mode is selected, the downloader uses a dedicated flow around:

```text
dist/platform.json
dist/nodes/list.json
```

---

## 6. Asset discovery

Asset discovery is layered:

1. direct project JSON fields;
2. nested JSON deep walk;
3. ICC Plus field sets and viewer config keys;
4. HTML/CSS/JS scan;
5. linked chunk/manifest traversal;
6. direct font CSS detection;
7. optional `yt-dlp` media recovery;
8. optional `gallery-dl` fallback;
9. optional Selenium/headless browser fallback;
10. optional AI-assisted JS asset candidate detection.

The goal is to continue through recoverable failures and report missing assets rather than crash the entire job.

---

## 7. Deep scan

Deep scan is enabled by default. It scans text-like assets and recursively follows same-origin or relevant project references. It attempts to discover assets that are not explicitly present in project JSON.

Disable it only when troubleshooting:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-deep-scan
```

---

## 8. Network and Cloudflare flow

Network setup can include:

- proxy mode;
- DNS/DoH override;
- BebasDNS preset;
- HTTP/2;
- Cloudflare mode;
- FlareSolverr endpoint/session/timeout/wait/proxy.

Cloudflare `auto` mode is intended to preserve normal requests first and use challenge-aware fallback only when signs of blocking are detected.

---

## 9. Safe file writing

URL-derived paths are sanitized before writing under the output root. Archive members are validated strictly before extraction/copying. Settings/cache files use atomic writes.

This reduces risk from:

- `../` traversal;
- absolute archive members;
- Windows drive prefixes;
- invalid filename characters;
- interrupted settings writes;
- duplicate or stale log handlers.

---

## 10. Concurrency model

Asset downloads use `ThreadPoolExecutor` with a configurable worker count:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --threads 4
```

The legacy pipeline still contains some current-working-directory-sensitive sections, so a global run lock serializes full `run_download` executions to reduce race risk.

---

## 11. Reports

Typical outputs:

- `cyoa_downloader.log` for runtime details;
- `backup_report.txt` for ICC/site download summaries;
- `failed_assets.txt` for failed asset details when backup report is unavailable;
- `failed_urls.txt` for batch failures;
- optional redacted settings exports.

---

## 12. Local preview

When `--serve` is used, the tool starts a localhost server for the output directory and can expose Serve Tools plus a bundled IntCyoaEnhancer-compatible helper route.

Preview helpers are local-only and intended for offline/debug/accessibility/QoL testing.

---

## 13. Post-download integrations

After the main download, optional integrations may run:

- CYOA Manager registration via `--cyoa-manager`;
- itch.io backend pass via `--itch` for itch URLs;
- local preview via `--serve`.

Recoverable integration failures should not invalidate the already-created CYOA output.
