# Changelog

This project uses a single changelog file. Older split release notes and patch reports have been consolidated here so users do not have to search through multiple Markdown files.

## v1.0.2 — stabilization + integrity verification (internal patch series rev18 → rev23)

A focused, additive release on top of `1.0.1`. The program version is now
`_APP_VERSION = "1.0.2"`. The rev18–rev23 labels below are internal patch markers for
traceability. Every change is backward-compatible: the main download behavior, CLI flags and
aliases, internal mode keys (`website_zip` / `website_folder`), output formats, and folder
layout are all unchanged. Self-test grew from 31/31 to **37/37**.

### Compatibility

- No change to the download concept, inputs, outputs, folder structure, existing CLI
  flags, or internal mode keys.
- New capabilities are opt-in and never alter the default download path.
- Legacy batch keywords (`website`, `website_zip`, `website_folder`) continue to work as
  aliases for the ICC keywords.

### New features

- **`--verify FOLDER`** — read-only integrity check for a finished output folder. Reports a
  broken/missing `project.json`, zero-byte assets, locally-referenced assets (in
  project.json / HTML / CSS / JS) that are missing on disk, and surfaces counts from any
  `failed_assets.txt` / `failed_images.txt`. Exit code `0` = intact, `1` = blocking issue.
- **`--write-manifest`** (used with `--verify`) — writes an opt-in `cyoa_manifest.json`
  checksum sidecar (sha256 + size per file). When present, `--verify` upgrades to full
  checksum verification (detects corrupted/truncated/modified files, not just missing
  ones). The manifest is never written during a normal download, so default output folders
  are unchanged.

### Bug fixes

- **Batch mode dispatch parity (rev18).** The GUI and CLI batch loops derived their
  `run_download` flags independently and had diverged: a batch row using the bare
  `pure_website` or `cyoap_vue` mode was silently mis-dispatched in the GUI (pure-website
  ran a normal embed/zip; cyoap_vue never triggered its probe). Consolidated both sites onto
  a single `_derive_mode_flags()` source of truth. CLI behavior is byte-identical; only the
  two previously-wrong GUI modes change.
- **Image-cache index race (rev19).** `_cache_load()` / `_cache_get()` accessed the shared
  cache index without the lock that guards every write. Added double-checked locking and a
  guarded read.
- **Widget-after-destroy `TclError` (rev20).** Worker-thread and timer callbacks could touch
  a widget after its window was destroyed. Added `_v25_safe_after_widget`, which re-checks
  the target widget at execution time, and routed five risky callbacks (auto-detect badge,
  two progress bars, Help copy button, viewer-register list refresh) through it.
- **RAR handle leak (rev22).** A RAR archive opened for `namelist()` was closed only on the
  success path, leaking the handle if the read raised. Switched to a context manager, matching
  the adjacent ZIP branch.
- **Auto-detect decode robustness (rev22).** A malformed server charset header
  (`charset=foobar`) raised `LookupError` and made the CYOAP probe reject an otherwise-valid
  JSON endpoint. Now also catches `LookupError` and retries a UTF-8 best-effort decode.
- **Queue cleared on unparseable status (rev23).** When a run's completion status string did
  not match the expected `… — N/M …` shape, the parse error was swallowed and execution fell
  through to the queue-removal path — potentially clearing the retry queue of a failed run.
  The parse-failure path is now conservative: it preserves the queue.
- **Mode-flag separator hardening (pre-release).** `_derive_mode_flags()` now normalizes dash
  and space separators (e.g. `icc-folder` → `icc_folder`) in addition to case, so a non-canonical
  mode string can no longer be silently mis-dispatched. Callers already passed canonical keys, so
  behavior on all existing paths is unchanged.
- **In-program guide corrections (text only).** Fixed the built-in Help / Setup / Import guide:
  removed stale internal dev-version labels (the guide no longer references an old `v7.x` build),
  added the missing "ICC Plus compatibility notes" section to the Indonesian guide so it matches
  the English one, corrected the Indonesian section numbering (now 0–8 with no gap), and documented
  the new `--verify` / `--write-manifest` commands in the diagnostics section of both languages.

### Tests

- Self-test expanded to **37/37**: added guards for batch mode-flag parity, image-cache
  load locking, after-callback destroy safety, the offline package validator, the manifest
  round-trip, decode robustness, and the queue-preservation policy.
- Standalone regression tests included: `test_rev18` (mode parity), `test_rev19` (cache
  concurrency), `test_rev20` (after-guard under Tk/Xvfb), `test_rev22` (handle/decode),
  `test_rev23` (queue policy).

### Notes

- A clean follow-up audit (rev23 follow-up) ran additional lenses (fall-through after
  swallowed exceptions, format-string mismatches, path-join escapes, daemon-thread
  correctness) and found no further issues — recorded as a deliberate no-ship.

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
