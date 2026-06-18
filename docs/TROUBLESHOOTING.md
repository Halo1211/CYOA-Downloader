# Troubleshooting

This guide helps diagnose installation problems, download failures, missing assets, batch import errors, GUI visual issues, Cloudflare/network problems, and bug-report preparation.

Start with the diagnostic commands:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

If you are new to the project, read [Getting Started](./GETTING_STARTED.md) first. For normal workflows, read [User Guide](./USER_GUIDE.md). For Cloudflare, proxy, AI, and media recovery, read [Advanced Features](./ADVANCED_FEATURES.md).

---

## 1. Diagnostic command checklist

| Command | What it checks | What to do if it fails |
| --- | --- | --- |
| `python cyoa_downloader.py --dependency-check` | Required and optional dependency status. | Install missing required dependencies; optional dependencies only if needed. |
| `python cyoa_downloader.py --self-test` | Offline sanity checks for core helpers. | Recheck file integrity and Python version. |
| `python cyoa_downloader.py --help` | CLI parser loads successfully. | Syntax/import issue if it fails. |
| `python -m py_compile cyoa_downloader.py` | Python syntax. | The file may be corrupted or patched incorrectly. |

Use these before reporting a bug.

---

## 2. Common setup problems

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `python` not found | Python is not installed or not in PATH. | Install Python 3.10+ and reopen the terminal. |
| `python` opens Microsoft Store on Windows | Windows app execution alias. | Use `py -3` or install Python from python.org. |
| `pip` installs globally by accident | Virtual environment is not active. | Activate `.venv` before installing. |
| PowerShell cannot activate `.venv` | Execution policy blocks scripts. | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| GUI does not open | Missing Tkinter or CustomTkinter. | Install requirements; on Linux install `python3-tk`. |
| Excel batch import fails | Missing `pandas` or `openpyxl`. | `pip install pandas openpyxl`. |
| HTTP/2 warning | Missing optional HTTP/2 extras. | `pip install "httpx[http2]"` or disable HTTP/2. |
| FFMPEG warning | `ffmpeg` not in PATH. | Install FFMPEG and confirm `ffmpeg -version`. |
| yt-dlp warning | Optional media extractor missing. | `pip install -U yt-dlp`. |

---

## 3. Download fails immediately

Check the URL:

- It must start with `http://` or `https://`.
- `file://`, `javascript:`, and `data:` are not valid source URLs for normal download.
- The page must be reachable from your network.
- Some sites block automation.

Try a conservative command:

```bash
python cyoa_downloader.py "https://example.com/project" --cloudflare off --no-ytdlp --workers 2 --wait 120
```

If this fails immediately:

1. Open the URL in a browser.
2. Check whether the page requires login.
3. Check whether the URL redirects to another domain.
4. Check whether your proxy/VPN is interfering.
5. Try the same command from another network if possible.

---

## 4. Project JSON not found

Possible causes:

- the site uses a custom viewer;
- the project is embedded in bundled JavaScript;
- the target page is not the actual viewer page;
- JavaScript rendering is required;
- the site changed structure;
- the page is blocked behind a challenge or login.

Try:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder
python cyoa_downloader.py "https://example.com/project" --pure-website-folder
```

Then inspect:

- downloaded HTML files;
- downloaded JavaScript bundles;
- `backup_report.txt`;
- `cyoa_downloader.log`;
- whether a `project.json` or embedded app bundle exists.

If the viewer downloads but the project is not detected, use Offline Viewer Center and Manual Inject. Details are in [User Guide](./USER_GUIDE.md).

---

## 5. Missing images or backgrounds

Possible causes:

| Cause | Explanation |
| --- | --- |
| CSS background images | Asset may be referenced from CSS instead of project JSON. |
| JavaScript-generated paths | Asset path appears only inside JS. |
| Hotlink protection | Server rejects asset requests outside the original context. |
| Rate limiting | Too many parallel requests. |
| Broken source asset | The original creator link is dead. |
| Unsafe filename/path | Path must be sanitized before writing locally. |
| Query-string variants | Same filename may have multiple URL variants. |

Try:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --threads 2 --wait-time 120
```

Then:

1. Open the missing asset URL in a browser.
2. Check failed asset logs.
3. Keep deep scan enabled.
4. Retry failed assets.
5. Check whether a proxy or Cloudflare mode is needed.
6. If paths are relative, check whether output folder structure matches viewer expectations.

---

## 6. Missing audio or video

Check tools:

```bash
yt-dlp --version
ffmpeg -version
```

Install/update:

```bash
pip install -U yt-dlp
```

Important notes:

- Direct audio files can often download without `yt-dlp`.
- YouTube/SoundCloud recovery depends on `yt-dlp`.
- FFMPEG may be needed for merge/conversion workflows.
- Missing FFMPEG should not break normal image backups.
- Media extractor behavior can change when source platforms change.

If media is not needed:

```bash
python cyoa_downloader.py "https://example.com/project" --no-ytdlp
```

---

## 7. Batch file problems

CSV/XLSX files need a URL-like column name:

- `url`
- `link`
- `urls`
- `links`

Example CSV:

```csv
url,filename,mode
https://example.com/cyoa,my_backup,website_folder
```

TXT example:

```text
https://example.com/cyoa-1
https://example.com/cyoa-2 | second_backup | website_folder
```

Common batch issues:

| Problem | Cause | Fix |
| --- | --- | --- |
| No URLs imported | Wrong column name. | Use `url` or `link`. |
| XLSX fails | Missing `pandas`/`openpyxl`. | Install both packages. |
| Some rows fail | Site-specific download issue. | Check `failed_urls.txt`. |
| Mode ignored | Unknown mode value. | Use supported mode aliases from [Getting Started](./GETTING_STARTED.md). |
| Filenames look wrong | Unsafe characters were sanitized. | Use simple filenames. |

---

## 8. Cloudflare or bot challenge

Try:

```bash
python cyoa_downloader.py "https://example.com/project" --cloudflare auto
```

If FlareSolverr is installed and running:

```bash
python cyoa_downloader.py "https://example.com/project" --cloudflare flaresolverr --flaresolverr-url http://localhost:8191/v1
```

Test FlareSolverr:

```bash
python cyoa_downloader.py --flaresolverr-test --flaresolverr-url http://localhost:8191/v1
```

Cloudflare behavior is unstable by design. A mode that works today may fail later if the site changes.

Read [Advanced Features](./ADVANCED_FEATURES.md) before using heavy recovery options.

---

## 9. Proxy, DNS, and network problems

Try disabling inherited proxy settings:

```bash
python cyoa_downloader.py "https://example.com/project" --proxy-mode disabled
```

Try manual proxy only if you know the proxy URL:

```bash
python cyoa_downloader.py "https://example.com/project" --proxy http://127.0.0.1:7890 --proxy-mode manual
```

Try DNS override if resolution is the problem:

```bash
python cyoa_downloader.py "https://example.com/project" --dns 1.1.1.1
```

Network debugging checklist:

1. Can your browser open the URL?
2. Is VPN/proxy enabled?
3. Does the site require login?
4. Does another network work?
5. Does reducing workers help?
6. Does disabling HTTP/2 help?

---

## 10. Offline viewer problems

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `index.html` opens blank | Browser blocked local `file://` behavior. | Use `--serve`. |
| Viewer opens but data missing | Project data not injected or wrong viewer template. | Use Manual Inject. |
| Images missing in offline viewer | Relative paths do not match viewer expectations. | Use ICC Folder and inspect paths. |
| Auto-match fails | Viewer/project structure is unusual. | Use Manual Inject. |
| Folder not created | Source data extraction failed. | Check logs and report. |

Serve example:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --serve --serve-port 0
```

Manual Inject details are in [User Guide](./USER_GUIDE.md).

---

## 11. GUI visual issues

### Dark-mode divider disappeared

The stabilized GUI uses a visible 2 px muted divider. If it still appears missing:

1. Confirm you are using the latest `cyoa_downloader.py` from the package.
2. Delete old generated build folders or cached copies.
3. Run from source, not an old EXE.
4. Test System, Dark, and Light themes.
5. Check display scaling.
6. Screenshot the toolbar when reporting the issue.

### Logo looks wrong

Expected logo files:

```text
assets/logo-light.png
assets/logo-dark.png
assets/logo-source.png
```

If the logo appears wrong:

- confirm `assets/` is next to `cyoa_downloader.py`;
- confirm original logo files were not replaced;
- launch from repository root;
- avoid running an older copied script;
- test both dark and light themes.

---

## 12. Settings problems

Export settings:

```bash
python cyoa_downloader.py --export-settings settings_export.json
```

Import settings:

```bash
python cyoa_downloader.py --import-settings settings_export.json
```

If settings seem corrupted:

1. Export settings first if possible.
2. Close the app.
3. Locate the user settings directory.
4. Move the settings file aside instead of deleting it.
5. Restart the app.
6. Reapply settings manually or import safe fields.

Secret fields should not be imported from exported settings.

---

## 13. Logs and issue reports

When reporting a bug, include:

- operating system;
- Python version;
- exact command used;
- GUI or CLI workflow;
- output mode;
- dependency-check output;
- self-test output;
- relevant log excerpt;
- screenshot for GUI issues;
- sample batch row for batch issues;
- whether `--workers 2 --wait 120` changes the result.

Do **not** include:

- API keys;
- cookies;
- authorization headers;
- private URLs;
- paid/private project links unless you have permission to share them.

---

## 14. Fast troubleshooting matrix

| Symptom | First try | Then try |
| --- | --- | --- |
| Download fails immediately | Check URL and browser access. | Disable proxy or try Cloudflare auto. |
| HTTP 429 | `--workers 2 --wait 120` | Add bandwidth limit. |
| Project JSON missing | `--icc-folder` | `--pure-website-folder`, then Manual Inject. |
| Missing images | Retry assets and keep deep scan enabled. | Inspect JS/CSS references. |
| Missing media | Check `yt-dlp` and FFMPEG. | Disable media if not needed. |
| Batch imports zero rows | Check URL column name. | Try TXT format. |
| GUI fails on Linux | Install `python3-tk`. | Check display server. |
| Logo/divider issue | Run latest source with assets folder. | Screenshot and report scaling/theme. |
