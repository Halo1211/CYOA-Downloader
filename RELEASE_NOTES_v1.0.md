# Release Notes — CYOA Downloader v1.0 Release

## Summary

CYOA Downloader v1.0 Release is the first GitHub-ready stable release package. It focuses on ICC naming consistency, stabilized download behavior, complete documentation, MIT licensing, and source/EXE distribution readiness.

## Highlights

- v1.0 Release branding.
- MIT License.
- Light/dark compatible logo in repository and GUI.
- GUI and CLI support.
- Full ICC ZIP and ICC Folder backup modes.
- Embedded JSON, ZIP, Both, Pure Website, and CYOAP Vue modes.
- Batch TXT/CSV/XLSX/XLS import.
- Asset scanning for images, fonts, audio, video, scripts, styles, and ICC Plus fields.
- Failed asset reports, backup reports, rotating logs, dependency check, and self-test.
- Local Serve preview and localhost helper policy.
- Cloudflare/FlareSolverr, proxy, DNS, HTTP/2, gallery-dl, yt-dlp, itch.io, AI Assist, and CYOA Manager documentation.
- Expanded GitHub documentation beyond bare minimum.

## CLI migration

Old commands no longer work:

```bash
python cyoa_downloader.py --website "URL" -o output
python cyoa_downloader.py -W "URL" -o output
python cyoa_downloader.py --website-folder "URL" -o output_folder
```

Use:

```bash
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

## Compatibility

These internal mode keys remain supported for batch/settings/manifest compatibility:

- `website_zip`
- `website_folder`

New batch files may use:

- `icc`
- `icc_zip`
- `icc_folder`

## Pre-release acceptance checklist

- `python -m py_compile cyoa_downloader.py`
- `python cyoa_downloader.py --help`
- `python cyoa_downloader.py --dependency-check`
- `python cyoa_downloader.py --self-test`
- `python -m pytest -q`
- GUI opens without arguments.
- `--icc` works.
- `--icc-folder` works.
- `--website`, `-W`, `--website-folder` fail intentionally.
- Old batch values `website_zip` and `website_folder` are still accepted.
- README and docs mention ICC Mode consistently.
- Logo appears in README and GUI.
