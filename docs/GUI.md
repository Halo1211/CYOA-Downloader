# GUI Guide — CYOA Downloader v1.0 Release

The GUI is intended for users who prefer a desktop workflow over CLI commands. Run it with:

```bash
python cyoa_downloader.py
```

or explicitly:

```bash
python cyoa_downloader.py --gui
```

---

## 1. Main GUI responsibilities

The GUI provides:

- single URL download;
- queue/batch-style workflow;
- output mode selection;
- ICC ZIP / ICC Folder selection;
- font download and analysis controls;
- logs/progress display;
- language preference;
- settings-driven feature toggles;
- local preview controls;
- userscript/helper information;
- CYOA Manager-related integration panels where available.

The v1.0 logo supports light and dark themes via:

```text
assets/logo-light.png
assets/logo-dark.png
```

If the asset files are unavailable, the application falls back to embedded logo data or text.

---

## 2. Recommended GUI workflow

1. Open the application.
2. Paste the CYOA/ICC URL.
3. Select output directory.
4. Choose **ICC Folder** first for large or unknown projects.
5. Enable fonts if needed.
6. Start download.
7. Review log and `backup_report.txt`.
8. Use local preview if needed.
9. If output is correct, run again as **ICC ZIP** for shareable packaging.

---

## 3. GUI mode meanings

| GUI label | Internal behavior | Recommended use |
| --- | --- | --- |
| Embedded JSON | `embed` | Basic project backup. |
| ZIP | `zip` | External asset ZIP. |
| Both | `both` | JSON + ZIP backup. |
| ICC ZIP | `website_zip` | Full offline ICC viewer ZIP. |
| ICC Folder | `website_folder` | Debuggable offline ICC folder. |
| Pure Website ZIP/Folder | Pure site mirror | Custom sites. |
| CYOAP Vue ZIP/Folder | CYOAP Vue flow | `dist/platform.json` and `dist/nodes/list.json` projects. |

The GUI should use **ICC** terminology. Older internal keys are preserved for compatibility only.

---

## 4. Queue and batch usage

The GUI can import queue items from TXT/CSV/XLSX/XLS files when available. Supported batch columns are the same as CLI:

| Column | Aliases |
| --- | --- |
| URL | `url`, `link`, `urls`, `links` |
| Filename | `filename`, `name`, `output`, `title`, `file` |
| Mode | `mode`, `output_mode`, `type` |

Recommended mode names for new files:

```text
embed, zip, both, icc, icc_zip, icc_folder,
pure_website_zip, pure_website_folder,
cyoap_vue_zip, cyoap_vue_folder
```

Old mode names `website_zip` and `website_folder` are still accepted.

---

## 5. Advanced feature toggles

Depending on the GUI build/panel state, users may control these settings:

| Feature | Meaning |
| --- | --- |
| Deep scan | Scan HTML/CSS/JS for hidden or indirect asset paths. |
| Playwright/Selenium headless fallback | Use browser rendering fallback when available. |
| Serve preview | Start local preview after download. |
| Bundled helper/cheat | Localhost-only offline preview helper. |
| gallery-dl | Optional external extractor fallback. |
| Cloudflare mode | Auto/cloudscraper/FlareSolverr handling. |
| AI Assist | Optional diagnostics/fallback assistance. |
| Proxy/DNS/BebasDNS | Network routing/resolver controls. |
| HTTP/2 | Optional deep-scan HTTP/2 fetches via `httpx[h2]`. |
| itch.io | Optional itch backend support. |

---

## 6. AI Assist in GUI

AI Assist is optional. Suggested safe defaults:

- provider: `ollama` for local testing or a cloud provider only if the user explicitly accepts that workflow;
- mode: `diagnostics` first;
- mode: `auto_fallback` when normal detection fails;
- budget: low call count and limited HTML/JS characters.

Recommended caution: do not use cloud AI providers for private content unless you are comfortable with the provider's policy.

---

## 7. Local preview and Serve Tools

If preview is enabled, downloaded output can be served through localhost. Serve Tools can expose:

- local preview controls;
- localStorage export;
- IndexedDB export;
- preview storage clear;
- bundled IntCyoaEnhancer-compatible helper;
- native local/offline helper panel.

These tools are intended for localhost/offline testing only.

---

## 8. Troubleshooting from GUI

If a download fails:

1. Check the GUI log.
2. Open the output folder.
3. Review `cyoa_downloader.log`.
4. Review `backup_report.txt` or `failed_assets.txt`.
5. Retry with ICC Folder.
6. Reduce worker count.
7. Increase 429 wait time.
8. Try Cloudflare auto/FlareSolverr if blocked.
9. Try gallery-dl smart if the URL is a supported gallery/post.
10. Use AI Assist only as a fallback.

---

## 9. EXE note

For a Windows `.exe` release, prefer distributing a zipped folder instead of a single-file EXE:

```text
CYOA-Downloader-v1.0-Windows-x64.zip
├─ CYOA Downloader.exe
├─ assets/
├─ README_FIRST.txt
└─ LICENSE
```

This is more transparent, easier to debug, and less likely to trigger false-positive antivirus behavior than a single compressed executable.
