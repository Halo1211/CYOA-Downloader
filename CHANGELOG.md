# Changelog

This project uses a single changelog file. Older split release notes and patch reports have been consolidated here so users do not have to search through multiple Markdown files.

## v1.0.1 — stabilization and GitHub cleanup

Version label in the program remains exactly:

```python
_APP_VERSION = "1.0.1"
```

### Compatibility

- Preserved the main download behavior from v1.0.
- Preserved legacy CLI flags and aliases.
- Preserved batch import behavior for TXT, CSV, XLSX, XLS, remote CSV, and Google Sheets CSV export URLs.
- Preserved Offline Viewer Center, Auto-match, Manual Inject, local serve preview, and userscript helper behavior.
- Preserved existing output concepts: embedded JSON, ZIP, both, ICC ZIP, ICC folder, pure website, and CYOAP Vue modes.

### Stabilization changes

- Expanded dependency diagnostics in `--dependency-check`.
- Added explicit `urllib3` reporting.
- Added clearer FFMPEG detection and non-fatal warnings.
- Clarified FFMPEG installation instructions for Windows, Linux, and macOS.
- Kept FFMPEG optional for normal downloads.
- Improved handling of optional dependencies such as `yt-dlp`, `customtkinter`, `Pillow`, `pandas`, `openpyxl`, `json5`, `tldextract`, and `httpx[http2]`.
- Added self-test coverage for dependency report behavior, unsafe URL schemes, and theme normalization.
- Kept the self-test offline and deterministic.

### GUI changes

- Default theme preference is now `System`.
- Theme switcher supports `System`, `Dark`, and `Light`.
- Theme selection is persisted in settings.
- Dark-mode toolbar divider uses a visible muted blue-grey line instead of a bright white line or invisible border.
- Original logo assets from the release package are retained in `assets/`.
- External logo loading remains optional; the application still opens if asset files are missing.

### GitHub repository cleanup

- Reworked the root README into the main serious entry point instead of a short placeholder.
- Removed duplicate `docs/README.md`; the root README is the only README entry point.
- Reduced the number of Markdown files by merging short documentation pages into five substantial docs.
- Consolidated changelog content into this single `CHANGELOG.md`.
- Removed patch-report files from the public root package.
- Kept issue forms, CI, examples, tests, assets, and screenshots.
- Kept docs English-only for GitHub consistency.

### Documentation structure after cleanup

- `README.md` — complete project overview and short start guide.
- `CHANGELOG.md` — single release history.
- `docs/GETTING_STARTED.md` — setup and first run.
- `docs/USER_GUIDE.md` — GUI, CLI, batch, offline viewer.
- `docs/ADVANCED_FEATURES.md` — AI Assist, Cloudflare, proxy/DNS/HTTP2, media recovery, theme/logo.
- `docs/TROUBLESHOOTING.md` — practical failure fixes.
- `docs/MAINTAINER_GUIDE.md` — tests, release discipline, and compatibility rules.

### Validation gates used for this stabilization line

- `python -m py_compile cyoa_downloader.py`
- `ast.parse`
- `python cyoa_downloader.py --help`
- `python cyoa_downloader.py --dependency-check`
- `python cyoa_downloader.py --self-test`
- `pytest -q`
- `ruff check cyoa_downloader.py --select F821`
- headless GUI smoke check for toolbar divider visibility when available

## v1.0 — release baseline

### User-facing release goals

- Provide a stable CYOA/ICC backup utility with both GUI and CLI workflows.
- Preserve core download behavior while adding clearer public documentation.
- Support beginner-friendly installation and usage documentation.
- Include tests and audit notes for maintainers.

### Major features

- GUI mode with URL input, queue, progress, logs, settings, retry controls, and preview/serve tools.
- CLI mode for direct downloads, batch jobs, diagnostics, and automation.
- Parallel image and asset downloads using `ThreadPoolExecutor`.
- ICC/CYOA project discovery from common viewer patterns.
- Asset scanning for images, CSS, JavaScript, fonts, audio, video, and common ICC Plus keys.
- Full website/offline viewer download modes.
- Dedicated CYOAP Vue backup modes.
- Batch import from TXT, CSV, XLSX, XLS, remote CSV, and Google Sheets export URL.
- Failed URL and failed asset reporting.
- Settings import/export with secret redaction.
- Dependency check and self-test entry points.
- Serve-only userscript helper integration for local preview workflows.

### Safety improvements in the v1.0 baseline

- URL scheme guard.
- Path traversal prevention for output paths.
- Strict archive member validation.
- Archive decompression limits.
- Atomic settings/cache writes.
- Rotating logs.
- Token/cookie/secret redaction in logs.
- Non-blocking GUI log queue.
- Thread-safe run serialization around legacy `os.chdir()` usage.

### Known remaining risks from the v1.0 line

- Live websites can change structure without notice.
- Some media sources require external tools such as `yt-dlp` and FFMPEG.
- Cloudflare-protected targets may require optional recovery tools or manual browser fallback.
- GUI visual behavior should still be checked on Windows, macOS, and Linux because Tk/CustomTkinter rendering differs by platform.
- Very large projects should be tested with folder output before ZIP output.
