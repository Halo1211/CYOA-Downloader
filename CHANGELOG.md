# Changelog

This project uses a single changelog file. Older split release notes and patch reports have been consolidated here so users do not have to search through multiple Markdown files.

## v1.1.2 — full-program bug audit

- Rebuilt the Windows executable and ZIP for v1.1.2. Verified embedded version
  metadata, matching ZIP/EXE hashes, packaged CLI diagnostics and self-test,
  and localhost folder/ZIP downloads with compressed CSS and manifest checks.
  Release downloads include SHA-256 checksums.
- Contained malformed resume state and itch.io offline manifests; an unavailable
  resume directory no longer aborts otherwise completed jobs.
- Handled failed remote batch requests, UTF-8 BOMs, and pipe-separated remote
  TXT lists without losing URL, filename, or output mode.
- Preserved URL path parameters and explicit port zero during canonicalization;
  domain backoff recovery now uses the same case-insensitive key as failures.
- Accepted drive-root output paths while retaining containment and link checks.
- Prevented ZIPs from including their own output or partial file, preserved
  literal percent-encoded filenames, and allowed highly compressible local
  content without relaxing validation of incoming archives.
- Continued route crawling after an interrupted response body and retained
  individual route failure details.
- Observed cancellation before committing a streamed file; GUI completion
  no longer counts an interrupted job twice or retains stale progress callbacks.
- Treated non-finite byte counts and compressed wire lengths as unknown in
  decoded-body progress, avoiding incorrect file totals and ETA estimates.
- Preserved concurrent cache replacements during stale reads and repaired
  corrupted cache files when their original content is downloaded again.
- Added offline regression coverage and enabled combined live GUI/browser
  smoke validation with Tk objects finalized on their owning test thread.
- A second audit fixed Discord reuse of empty files/directories, contained
  destination errors, closed HTTP error streams, handled invalid API encoding,
  and observed cancellation during streams and retry backoff.
- AI calls now observe cancellation before and after provider requests and
  treat null response content as unavailable instead of the text "None".
- DNS queries use IDNA byte lengths; wire replies reject query, truncated,
  and error packets, and the UDP fallback follows CNAME answer chains.
- Website recovery preserves parentheses in asset URLs, skips malformed
  manifests and unavailable folders, isolates session cleanup errors, and
  includes skipped assets in discovered totals.
- CSV/Excel queue imports preserve literal filenames such as `00017`, `NA`,
  and `NULL`. Cancelled GUI asset recovery releases its running state.
- Retry Images handles JSON-escaped URLs, rejects empty image bodies, and
  writes project updates atomically to preserve the original on commit failure.
- Consolidated 71 test files into `tests/test_program.py`, preserving all 717
  collected cases, independent helper names, optional smoke-test marks, and
  the shared GUI fixture's lifetime. Feature sections remain selectable with
  pytest's `-k` option.
- A third audit added 49 regression and control cases to the same test file.
  Atomic writes now use exclusive temporary files, preserving unrelated partial
  files and avoiding filename collisions.
- Log redaction covers short credentials, signed URL parameters, exception
  tracebacks, and stack details without removing useful diagnostic context.
- Settings accept UTF-8 BOMs, reject non-finite AI temperatures, and report
  failed import transactions instead of raising into the caller.
- History retention tolerates malformed timestamps; history and update probes
  share identity-length handling, preserve zero-byte sizes, avoid compressed
  length false positives, and display the recorded download date.
- DNS preserves nested bypass state even when session cleanup fails, validates
  DoH ports, handles IPv6 UDP resolvers and AAAA fallback, and returns both
  enabled address families while keeping localhost on the system resolver.
- Update checks isolate callback and response cleanup failures while preserving
  cancellation. Gallery-dl rejects non-HTTP inputs and terminates its active
  subprocess when cancelled.
- Asset recovery continues across localization and report-writing failures;
  failed localization leaves affected assets pending for another attempt.
- Third-audit validation with optional live GUI/browser checks enabled: 762 passed, 4
  skipped (Windows symlink creation unavailable). Offline self-test: 37/37.
  Compilation, CLI help, dependency checks, repository Ruff checks, and diff
  whitespace checks also passed.
- A fourth audit added 43 regression and control cases to the consolidated
  test file. Font discovery retains query/fragment URLs, ignores malformed
  references, and continues after individual stylesheet failures.
- Font downloads reject empty or incomplete identity bodies, preserve
  cancellation, continue after individual write failures, and rewrite
  overlapping plain/JSON-escaped URL aliases in one pass.
- Asset scanner plugins reject invalid return shapes and non-string entries;
  engine detectors ignore non-dictionary results so later detectors still run.
- Unusable Node probes no longer abort other available audio runtimes. Audio
  progress hooks observe cancellation even without a GUI callback.
- Audio reuse requires a nonempty file with the exact expected name. Conversion
  commits a validated MP3 atomically only after ffmpeg succeeds; failures retain
  the source audio and remove temporary outputs.
- Proxy profiles reject port zero. Shared HTTP pool reset continues closing
  other sessions after an individual cleanup failure and preserves cancellation.
- Fourth-audit standard suite: 801 passed, 8 skipped; offline self-test: 37/37.
  Compilation, CLI help, dependency checks, repository Ruff checks, and diff
  whitespace checks passed. Combined GUI runs exposed native Tcl initialization
  failures after earlier layout roots; the performance smoke now executes its
  assertions in a fresh process matching the application's single-root lifecycle.
- Final fourth-audit suite with live GUI/browser checks enabled: 805 passed,
  4 skipped because Windows symlink creation is unavailable.
- A fifth audit added 49 regression/control cases to `tests/test_program.py`.
  Archive resume closes its downloader on success, failure, and cancellation,
  handles malformed page collections, and rejects entries outside the story.
- Local previews preserve content query variants, including the root route,
  while ignoring tracking, RSC, cache, token, and preview-tools parameters.
  Tests exercise the real CLI HTTP handler on localhost.
- Embedded JSON5 extraction ignores braces inside JavaScript comments. Project
  and manifest readers contain excessive JSON nesting instead of crashing.
- Invalid request types, malformed hosts, and invalid ports are rejected before
  creating a session. CDN headers support trailing-dot DNS names. Abandoned
  response cleanup preserves the original cancellation exception.
- Viewer registration reports copy, lock, storage, and manifest failures;
  failed imports roll back archive replacements, including folder imports.
  Unregistration saves metadata before deleting the ZIP and reports failed
  registry transactions without consuming cancellation.
- Fifth-audit validation: standard suite 850 passed, 8 skipped; live GUI/browser
  suite 854 passed, 4 skipped (Windows symlink creation unavailable). Offline
  self-test 37/37; compilation, CLI help, dependency, Ruff, and diff checks passed.
- A sixth audit added 51 regression/control cases to `tests/test_program.py`.
  Responsive image discovery keeps fallback `src` and every `srcset` candidate,
  handles unquoted HTML attributes and character references, and skips inline
  data images without losing later candidates. Literal punctuation in direct
  image URLs is preserved. Large-project scanning retains CSS, audio, and video
  poster references without duplicating HTML-encoded URLs.
  The HTML/JavaScript scanner shares the same srcset token parser, preserving
  commas inside URLs and preventing inline Base64 fragments from becoming
  bogus download requests.
- Resume and cache readers contain excessive JSON nesting; cache index merging
  repairs an overdeep disk index. Unserializable resume data is reported without
  aborting the job. Asset-manifest traversal is iterative so valid nested
  projects retain their assets beyond Python's function-call depth limit.
- Stream and ZIP transactions use exclusive temporary files and preserve
  unrelated partial files on both success and failure. ZIP creation observes
  cancellation again after validation, before replacing an existing output.
- The main download orchestrator explicitly closes its website browser transport
  on success, failure, and cancellation, isolates ordinary cleanup failures,
  restores the working directory, and releases the output lock. Auto engine
  probing propagates cancellation instead of starting another resolver.
- Final sixth-audit validation with live GUI/browser checks enabled: 905 passed,
  4 skipped because Windows symlink creation is unavailable. All 51 new cases
  passed; offline self-test 37/37. Compilation, CLI help/dependency checks,
  repository Ruff checks, test formatting, and diff whitespace checks passed.

- A seventh audit added 45 regression/control cases to `tests/test_program.py`.
  Static-gallery responses close on every validation and streaming path;
  ordinary cleanup failures preserve the original result or cancellation.
  The gallery executor shuts down after submission errors, and invalid or
  non-finite worker counts use safe defaults at both download entry points.
- Remote batch streams observe cancellation before, during, and after body
  reads. Response cleanup no longer replaces successful imports or read errors.
  TXT imports preserve commas in URL queries/fragments while retaining legacy
  CSV rows with headers or explicit modes; malformed URLs do not stop later jobs.
- Timed-out VPN interface discovery returns unverified fallback interfaces,
  preserving the fail-closed guard. GUI speed callbacks propagate cancellation.
- Settings and history contain excessive JSON nesting on both read and save
  paths, preserving existing files after serialization failure. Settings imports
  now report actual commit failures instead of claiming unsaved changes succeeded.
- Final seventh-audit validation with live GUI/browser checks enabled: 950 passed,
  4 skipped because Windows symlink creation is unavailable. All 45 new cases
  passed; offline self-test 37/37. Compilation, CLI help/dependency checks,
  repository Ruff checks, test formatting, and diff whitespace checks passed.

## v1.1.1 — offline reliability and itch.io HTML5 support

- Resolved origin-root asset URLs and retried missing root-relative entry
  scripts correctly in offline website packages.
- Rejected unsafe parent paths in locally saved font CSS before downloading or
  copying a font file.
- Rebuilt CYOA Manager Serve previews when a registered viewer archive changes;
  a locked or unreadable import ZIP now returns a reported failure.
- Detected CYOA Manager libraries in the standard Windows installer location.
- Reported an empty itch-dl result as a failure even if its process exited with
  code zero.
- Prepared downloaded itch.io HTML5 ZIPs as cached offline folders while
  retaining the original archives; encrypted ZIPs are left intact and reported.
- Corrected itch.io API key guidance and connectivity reporting so a reachable
  public page is not presented as a download-ready backend.
- Contained interrupted HTML responses while validating script/style assets,
  so one failed body does not abort the website mirror.

## v1.1.0 — offline archives, local previews, and integration reliability

- Fixed catalog resolution and asset caching, including concurrent downloads
  of the same resource and offline folder/ZIP outputs.
- Expanded pure website archiving for JavaScript route trees and local asset
  references. Verified folder and ZIP variants offline.
- Improved CYOAP Vue discovery of external styles, JSON, images, and media.
  Verified multiple outputs with viewer options on and off; source asset
  references are retained in the offline copies.
- Added CYOA Manager JSON and ZIP import compatibility, local library browsing,
  and Serve previews for JSON-only entries. Preview asset caches are isolated by
  project, and Manager ZIPs receive decompression and path safety checks.
- Expanded the localhost Serve cheat panel with choice search, individual
  selection, soft select all, reset, point editing, requirement unlocking, and
  restoration of original local preview state.
- Upgraded the optional itch-dl wrapper with installed-backend preference,
  mirror and parallel options, cancellation, accurate file counts, and masked
  API keys. Connectivity tests now report network failure correctly.
- Refreshed AI Assist model recommendations and verified all nine provider
  transports with mocks; no paid API calls were needed.
- Restored the public GitHub release update endpoint and made update-check
  failures visible instead of displaying a false “up to date” result.
- Synchronized the runtime, Windows version metadata, tests, and English
  documentation on 1.1.0. Corrected the executable's embedded license label.

The live download matrix used each source once and derived viewer and archive
variants locally. CYOA Manager's own UI and a live itch.io game download were
not exercised because the Manager app and an itch.io target were unavailable.

## v1.0.9 — safe offline viewer modernization

- Added opt-in offline-viewer automation with independent switches for
  JSON-only downloads and full ICC website modernization. Both remain off by
  default.
- Added compatibility-aware selection for ICC Original, ICC Plus Legacy,
  ICC Plus 2, ICC Remix, and Lt. Ouroumov-derived viewers. ICC Original and
  ICC Plus Legacy have separate Settings cards, registry types, schema
  detection, and templates. ICC Plus 2 accepts only a
  verified local/offline/standalone runtime and never falls back to its online
  viewer.
- Added registration for viewer ZIP, RAR, and unpacked folders, plus a themed
  Settings checklist showing required and optional viewer-family coverage.
- Preserved publisher titles, favicons, fonts, loading CSS, inline styles, and
  unrelated scripts during runtime replacement. Original overwritten files
  are retained under `__original_site__`.
- Made Plus 2 and Remix modernization repeat-safe. A second run refreshes the
  embedded project without requiring another Remix template or changing the
  first original backup.
- Validate replacement templates before an in-place overlay so an invalid
  archive cannot leave a partially modified website.
- Removed missing-image extension substitution and all generated missing-asset
  placeholders. Failed references remain exactly as authored and are recorded
  in `failed_assets.txt`, `failed_images.txt`, `backup_report.txt`, or
  `skipped_youtube_audio.txt`.
- Narrowed obsolete-runtime filtering so publisher files such as
  `app.publisher-hooks.js` and `app.publisher-theme.css` are not discarded as
  generated bundles.
- Added failure reporting and cross-origin private-host protection to the
  preserved-viewer asset localizer.
- Reduced idle GUI work and bounded log/progress rendering per event-loop tick
  to keep the main window responsive on lower-specification computers.
- Fixed the Linux CI setup so unrelated Chrome APT mirror metadata cannot block
  dependency installation.
- Expanded the offline regression suite to 505 passing tests with 8 optional
  tests skipped when their runtime conditions are unavailable.

## v1.0.8 — website reliability and advanced network profiles

- Reduced CYOA.CAFE slug resolution to the authoritative slug lookup instead
  of first sending a guaranteed-failing PocketBase record-ID request.
- Added a bounded negative metadata cache so repeated detection stages do not
  request the same unavailable catalogue record again within one run.
- Refresh stale CYOA.CAFE alias records before trusting a cached viewer target.
- Fixed manual proxy bypass rules across Requests, DoH, browser, FlareSolverr,
  and gallery-dl paths; FlareSolverr sessions are now isolated by a hashed
  effective proxy route so profile changes cannot reuse an old route.
- Added `--version` for quickly checking which CLI/EXE build is running.
- Embedded File Version and Product Version in Windows executable properties.
- Added an offline catalog resolver regression that requires exactly one
  metadata lookup and one bounded viewer validation.
- Fixed the concurrent website-asset cache bug that could treat an in-progress
  marker as a filesystem path and repeatedly break interactive-site downloads.
- Fixed Windows GitHub builds that mistook ordinary 8.3 path aliases such as
  `RUNNER~1` for symlinks or junctions while keeping real reparse-point guards.
- Fixed Linux CI imports by invoking pytest through the selected Python
  interpreter, and upgraded official GitHub Actions to their Node.js 24 majors.
- Added synchronized advanced network settings for the main GUI, Settings
  dashboard, CLI, Requests sessions, and browser fallbacks.
- Added environment/manual/disabled proxy profiles, per-scheme HTTP/HTTPS
  overrides, bypass hosts, credential redaction, SOCKS4/5, and `socks5h`.
- Added system, UDP, TCP, DNS-over-HTTPS, and DNS-over-TLS transports with
  Cloudflare, Google, Quad9, BebasDNS, and custom resolver presets.
- Kept DoT certificate hostname verification enabled and added explicit DNS
  fallback, timeout, port, and IPv6 controls.
- Added an application-level fail-closed VPN interface guard without claiming
  to create or manage an operating-system VPN tunnel.
- Replaced the previous third-party favicon network test with offline settings
  validation that checks formats and local interfaces only.
- Updated the embedded Help/Guide and repository documentation in English,
  including detailed proxy, DNS privacy, VPN routing, and offline-validation
  explanations.
- Expanded the offline regression suite to 438 passing tests with 7 optional
  tests skipped when their runtime conditions are unavailable.

## v1.0.7 — release consistency and beginner-friendly workflow

- Synchronized the runtime, `VERSION` file, documentation, tests, issue
  templates, and release badge on v1.0.7.
- Added an English beginner guide covering the first download, output modes,
  troubleshooting, source setup, release checks, and Windows packaging.
- Updated the Windows build script to run release checks, support repeat-build
  switches, and clearly report each build stage.
- Updated the Windows GitHub Actions workflow to use the same tested local
  build script used by maintainers.
- Refreshed regression tests to match the current ICC labels, readable legacy
  console output, and safe atomic `.part` cleanup behavior.

## v1.0.6 — runtime diagnostics, Windows packaging, and CYOA.CAFE fixes

- Expanded Diagnostics to cover `yt-dlp-ejs`, JavaScript runtimes, browser
  backends, Playwright Chromium, RAR helpers, FFmpeg, and PyInstaller resources.
- Added actionable YouTube extraction errors and Windows runtime discovery.
- Added a reproducible Windows build and GitHub Actions artifact build.
- Switched the Windows package to a single-file executable and added the
  transparent black logo as its multi-resolution Windows icon.
- Removed generated release ZIP and obsolete historical source copies from the
  repository tree.
- Updated CYOA.CAFE discovery to support current human-readable `/game/<slug>`
  links as well as legacy PocketBase record IDs.
- Hardened archive handling against oversized ZIP metadata, unsafe Windows
  member names, path traversal through links/junctions, and linked files during
  packaging.
- Prevented cleanup from deleting legitimate user files ending in `.part`;
  only downloader-generated atomic temporary files are removed.
- Added safer CYOAP Vue path handling, worker limits, cross-origin internal-host
  blocking, and consistent HTTP error handling for Cloudflare fallbacks.

## v1.0.5 — GUI queue editing and export

### New features

- Queue row mode badges are now clickable, allowing a URL to switch between
  output modes without removing and re-adding the job.
- Added **Export List…** for CSV/TXT queue backups containing `url`,
  `filename`, and `mode`. Exported `auto` modes round-trip through the existing
  importer.
- Added [`docs/GUI_QUEUE_GUIDE.md`](docs/GUI_QUEUE_GUIDE.md) and updated the
  GUI/README documentation.

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
