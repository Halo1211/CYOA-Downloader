# User Guide

This guide explains the normal workflows for CYOA Downloader after installation is complete. It covers the GUI, CLI, output modes, reports, retry tools, batch import, Offline Viewer Center, Manual Inject, local serving, and settings.

If you have not installed the project yet, start with [Getting Started](./GETTING_STARTED.md). If something fails, use [Troubleshooting](./TROUBLESHOOTING.md).

> **First time?** Do one download with **ICC Folder** before changing advanced
> settings. It creates the easiest output to inspect and retry.

---

## 1. Workflow overview

CYOA Downloader can be used in two main ways:

| Interface | Best for | Notes |
| --- | --- | --- |
| GUI | Beginners, visual workflows, queue management, offline viewer tools, retry buttons | Running `python cyoa_downloader.py` without arguments opens the GUI. |
| CLI | Scripts, batch jobs, reproducible tests, advanced flags | Use `python cyoa_downloader.py --help` for the complete flag list. |

Recommended first workflow:

1. Run `--dependency-check`.
2. Open the GUI.
3. Download one project using ICC Folder.
4. Inspect the output folder.
5. Read the report.
6. Retry failed assets if necessary.
7. Move to CLI/batch only after one URL works.

---

## 2. GUI overview

The GUI is organized around practical tasks:

- paste or queue URLs;
- choose output mode;
- start a download;
- inspect live logs;
- retry failed assets;
- manage offline viewers;
- use Manual Inject for difficult viewer/project combinations;
- adjust theme, workers, language, and optional tools.

The default theme follows the operating system. If the OS is in dark mode, the interface uses dark panels with a muted visible divider between the action bar and feature tabs.

---

## 3. Basic GUI workflow

1. Start the app:

```bash
python cyoa_downloader.py
```

2. Paste a CYOA URL.
3. Choose an output mode.
4. Click **Download All**.
5. Wait for the log to finish.
6. Open the output folder.
7. Read `backup_report.txt` or the generated report.
8. Use retry tools for failed files.

For most users, **ICC Folder** is the best first mode because it produces inspectable files.

---

### Change a queued mode or export the queue

You do not need to remove a URL just because you changed your mind about its
output mode. Click the row's mode badge, choose a new mode, and keep working.
You can also edit the filename directly in the row.

Use **Export List…** to save the queue as CSV or TXT. The saved columns are
`url`, `filename`, and `mode`, and the file can be loaded again with
**Import List…**. See the [GUI Queue Guide](./GUI_QUEUE_GUIDE.md) for a
beginner example.

## 4. Choosing an output mode

| Goal | Recommended mode | Why |
| --- | --- | --- |
| Quick test | Default backup | Minimal command, good first check. |
| Inspect project and assets | ICC Folder | Easy to browse and debug. |
| Share a packaged backup | ICC ZIP | Convenient archive output. |
| Recover a custom viewer | Pure Website Folder | Saves viewer/site files for inspection. |
| CYOAP Vue project | CYOAP Vue Folder or ZIP | Uses dedicated CYOAP Vue handling. |
| Unsure which format is needed | Both | Produces more compatibility formats. |

Do not use ZIP-only workflows when debugging. Folder output is easier to inspect.

---

## 5. Reports and logs

Common output files:

| File | Purpose |
| --- | --- |
| `backup_report.txt` | Main backup summary: source URL, project URL, downloaded files, failed files, notes. |
| `failed_assets.txt` | Failed asset list when no backup report exists. |
| `failed_urls.txt` | Batch mode failed URL log. |
| `cyoa_downloader.log` | Rotating application log. |

Before sharing logs publicly, review them manually. The application redacts common secret patterns, but users should still avoid sharing private URLs, cookies, API keys, or tokens.

### Verifying a backup is complete

After a backup finishes, you can confirm it is intact **without downloading it again**:

```bash
python cyoa_downloader.py --verify "path/to/output_folder"
```

This read-only check reports missing referenced assets, empty (zero-byte) files, and a broken or missing `project.json`. It prints `PASS`/`FAIL` and exits with code `0` (intact) or `1` (problem found), so you can use it in scripts.

For the strongest check — detecting files that became **corrupted or truncated**, not just missing — record a checksum baseline once, then verify whenever you like:

```bash
# 1. Once, right after a clean download:
python cyoa_downloader.py --verify "path/to/output_folder" --write-manifest
# 2. Any time later:
python cyoa_downloader.py --verify "path/to/output_folder"
```

The `cyoa_manifest.json` baseline is **opt-in** and is never written during a normal download, so your output folders are unchanged unless you ask for it.

---

## 6. Retry tools

Retry tools are used when a backup mostly succeeds but some assets fail.

| Tool | Use when |
| --- | --- |
| Retry Assets | General failed assets are listed in the report/log. |
| Retry Images | Images/backgrounds are missing but project data exists. |
| Retry Audio | Audio/media files fail and optional tools are installed. |
| Batch Check | You imported multiple URLs and need to inspect queue status. |

Retry success depends on:

- whether the asset URL still exists;
- whether the source blocks hotlinking;
- whether the request is rate-limited;
- whether optional extractors are installed;
- whether the asset path was detected correctly.

---

## 7. Offline Viewer Center

Offline Viewer Center helps produce local viewer folders that can run offline or through a local server.

Normal order:

1. Use Auto-match.
2. Check whether the viewer template matches the project data.
3. Build `<name>_offline/`.
4. Open `index.html` locally or serve it through localhost.
5. If Auto-match fails, use Manual Inject.

Expected output:

```text
<name>_offline/
  index.html
  assets/
  project data...
```

The folder name pattern `<name>_offline/` is compatibility-sensitive and should not be changed without a migration note.

---

## 8. Manual Inject

Manual Inject is for difficult projects where automatic viewer matching is not enough.

Accepted source types may include:

| Source type | Example |
| --- | --- |
| Project JSON file | `project.json` |
| Folder | folder containing project data/viewer data |
| ZIP archive | downloaded archive containing project data |
| URL | URL that resolves to project data |
| Embedded app bundle | `app.xxx.js` or similar bundle that can be parsed |

Manual Inject reuses existing extraction helpers where possible. It should remain additive and should not replace normal Auto-match.

---

## 9. Local serve preview

Some browser features work differently under `file://`. Local serve preview runs the output through `http://localhost`, which is closer to normal web behavior.

CLI example:

```bash
python cyoa_downloader.py "https://example.com/project" --icc-folder --serve --serve-port 0
```

`--serve-port 0` lets the application choose an available port.

Use local serve when:

- `index.html` opens but assets do not load under `file://`;
- browser security blocks local scripts/assets;
- you want to test bundled userscript helper behavior;
- you need a stable localhost preview for debugging.

---

## 10. CLI workflows

Show all flags:

```bash
python cyoa_downloader.py --help
```

Common workflows:

| Workflow | Command |
| --- | --- |
| Default backup | `python cyoa_downloader.py "https://example.com/project"` |
| ZIP backup | `python cyoa_downloader.py "https://example.com/project" --zip` |
| Both JSON and ZIP | `python cyoa_downloader.py "https://example.com/project" --both` |
| ICC ZIP | `python cyoa_downloader.py "https://example.com/project" --icc` |
| ICC folder | `python cyoa_downloader.py "https://example.com/project" --icc-folder` |
| Pure website folder | `python cyoa_downloader.py "https://example.com/project" --pure-website-folder` |
| CYOAP Vue folder | `python cyoa_downloader.py "https://example.com/project" --cyoap-vue-folder` |
| Batch file | `python cyoa_downloader.py --list examples/batch_urls.csv --output downloads` |
| Lower request pressure | `python cyoa_downloader.py "URL" --workers 2 --wait 120` |
| Disable media extraction | `python cyoa_downloader.py "URL" --no-ytdlp` |
| Serve result | `python cyoa_downloader.py "URL" --icc-folder --serve` |

For the full table of options, read [Getting Started](./GETTING_STARTED.md).

---

## 11. Batch import

Supported file types:

- `.txt`
- `.csv`
- `.xlsx`
- `.xls`
- remote CSV URL
- Google Sheets CSV export URL

Supported URL columns:

- `url`
- `link`
- `urls`
- `links`

Supported filename columns:

- `filename`
- `name`
- `output`
- `title`
- `file`

Supported mode columns:

- `mode`
- `output_mode`
- `type`

Common mode values:

| Mode | Meaning |
| --- | --- |
| `embed` | Embedded JSON-style output. |
| `zip` | ZIP with external images. |
| `both` | Embedded and ZIP outputs. |
| `website_zip` | Full website/viewer ZIP. |
| `website_folder` | Full website/viewer folder. |
| `pure_website_zip` | Pure website ZIP. |
| `pure_website_folder` | Pure website folder. |
| `cyoap_vue_zip` | CYOAP Vue ZIP. |
| `cyoap_vue_folder` | CYOAP Vue folder. |

If a batch file has no URL column, the program should report it clearly. If only some rows fail, check `failed_urls.txt`.

---

## 12. Settings

Settings are stored under the user profile, not inside the repository.

Common settings include:

- theme mode;
- language preference;
- worker count;
- optional AI provider/mode;
- optional proxy/network behavior;
- output preferences;
- optional feature toggles.

Export settings with secrets redacted:

```bash
python cyoa_downloader.py --export-settings settings_export.json
```

Import settings:

```bash
python cyoa_downloader.py --import-settings settings_export.json
```

Secret fields are intentionally ignored during import.

---

## 13. Theme and logo basics

Theme modes:

- System
- Dark
- Light

The default is **System**. This keeps the app aligned with the OS appearance.

Logo files live in:

```text
assets/logo-light.png
assets/logo-dark.png
assets/logo-source.png
```

If the GUI is launched from outside the repository root, make sure the `assets/` folder is still next to `cyoa_downloader.py`.

More details are in [Advanced Features](./ADVANCED_FEATURES.md).

---

## 14. When to read other guides

| Situation | Next document |
| --- | --- |
| Installation or first run is confusing | [Getting Started](./GETTING_STARTED.md) |
| Site blocks the downloader | [Advanced Features](./ADVANCED_FEATURES.md) and [Troubleshooting](./TROUBLESHOOTING.md) |
| Images/audio are missing | [Troubleshooting](./TROUBLESHOOTING.md) |
| You are editing code or docs | [Maintainer Guide](./MAINTAINER_GUIDE.md) |
