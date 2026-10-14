# Changelog

User-facing changes are listed by release. Current setup and troubleshooting
instructions are in [README.md](README.md) and the linked guides.

## v1.1.4 — October 14, 2026

- Preserve an explicitly selected Classic strategy when saving settings.
- Honor environment proxy scheme overrides and `NO_PROXY` in HTTP, DoH,
  FlareSolverr, and browser transports. Rebuild reusable browser sessions when
  their proxy profile changes and verify TLS in all Playwright paths.
- Keep Selenium diagnostics offline and handle unavailable driver helpers.
- Contain failed GUI worker setup, retain failed status, and finalize the run.
- Reject truncated remote batch bodies and handle unreadable local TXT lists.
- Decode Unicode BOMs without relying on optional charset detectors.
- Reject Windows path aliases, ZIP links, duplicate paths, and file/directory
  conflicts before extraction. Skip cache junctions during cleanup.
- Preserve literal filenames during local copying, ZIP creation, and manifest
  verification; encode local route links before adding fragment anchors.
- Keep crawling inside the decoded story scope and recognize default ports.
- Recover later project candidates after a damaged ZIP member, preserve runtime
  assets after browser cleanup errors, and close abandoned HTTP responses.
- Observe cancellation before and after browser navigation and body reads.
- Consolidate English documentation and remove outdated and duplicate guides.
- Add 83 regression/control cases. Local validation: 1,034 tests and all 37
  self-tests passed, with four Windows symlink tests skipped. Live GUI/browser
  checks and all five CLI output variants passed.

## v1.1.3 — cross-platform previews

- Skip invalid manifest paths before resolving previews, fixing Linux failures
  while allowing later valid entries to resolve the requested route.
- Refresh Windows packages and release checksums.

## v1.1.2 — download reliability

- Contain malformed settings, resume state, history, and recovery manifests.
- Improve remote batch imports, Unicode handling, and literal CSV/Excel values.
- Preserve unrelated partial files and existing outputs during atomic writes.
- Fix ZIP self-inclusion, compressed-content checks, and cancellation before
  committing output files.
- Improve progress totals, cache concurrency, response cleanup, and GUI results.
- Harden DNS parsing, IPv6, CNAME handling, proxy validation, and VPN discovery.
- Improve Discord, AI, gallery-dl, font, and audio error handling.
- Recover missing website assets and interrupted route archives independently.
- Improve viewer registry transactions, project extraction, responsive image
  discovery, and query-aware previews.
- Consolidate the main regression suite in `tests/test_program.py`.

## v1.1.1 — offline reliability and itch.io HTML5 support

- Resolve origin-root assets and retry missing entry scripts in website packages.
- Protect local font paths and contain interrupted HTML responses.
- Refresh CYOA Manager previews when their viewer archive changes.
- Detect standard Windows CYOA Manager installations.
- Report empty itch-dl results as failures and clarify API key requirements.
- Prepare HTML5 ZIPs as cached offline folders while retaining original archives.

## v1.1.0 — offline archives and local previews

- Improve catalog resolution, concurrent asset caching, JavaScript routes, and
  CYOAP Vue discovery.
- Add CYOA Manager JSON/ZIP compatibility, library browsing, and local previews.
- Expand local preview helpers with choice search, point editing, and reset.
- Improve itch-dl backend selection, cancellation, parallelism, and reporting.
- Update AI Assist presets and make release-check failures visible.

## v1.0.9 — offline viewer modernization

- Add opt-in JSON viewer injection and full ICC website modernization.
- Select compatible ICC Original, Plus Legacy, Plus 2, Remix, and Lt. Ouroumov
  runtimes; require local/offline bundles for ICC Plus 2.
- Register viewer ZIPs, RARs, and folders, and preserve publisher assets and
  original overwritten files.
- Make modernization repeat-safe and validate templates before overlaying files.
- Preserve failed asset references instead of substituting placeholders.
- Reduce idle GUI work and improve Linux CI package installation.

## v1.0.8 — website reliability and network profiles

- Improve CYOA.CAFE metadata resolution and concurrent website asset caching.
- Fix manual proxy bypass and isolate FlareSolverr sessions by effective route.
- Add `--version` and Windows executable version metadata.
- Add synchronized network profiles, proxy scheme overrides, DNS transports,
  IPv6 controls, and a fail-closed VPN interface guard.
- Replace public favicon tests with offline configuration validation.
- Fix Windows build path checks and Python selection in Linux CI.

## v1.0.7 — release consistency

- Synchronize runtime metadata, Windows resources, documentation, and tests.
- Improve first-run guidance and Windows build checks.
- Use the same Windows build script locally and in GitHub Actions.

## v1.0.6 — runtime diagnostics and packaging

- Expand diagnostics for media runtimes, browsers, RAR helpers, and frozen apps.
- Improve YouTube extraction errors and Windows runtime discovery.
- Add reproducible Windows packaging and GitHub build automation.
- Improve CYOA.CAFE resolution and local preview handling.

## v1.0.5 — GUI queue editing and export

- Edit a queued job's output mode without removing its URL.
- Export queues as CSV or TXT with filenames and modes intact.

## v1.0.2 — integrity verification

- Add `--verify` and opt-in `--write-manifest` checksums.
- Unify GUI/CLI batch-mode dispatch and normalize mode aliases.
- Fix image-cache locking, callbacks after window destruction, RAR cleanup,
  malformed charset handling, and accidental queue clearing.
- Expand offline self-tests and correct the in-app guides.

## v1.0.1 — stabilization

- Improve dependency diagnostics and optional FFmpeg handling.
- Persist System, Dark, and Light themes and improve the dark-mode divider.
- Retain bundled logo assets and handle missing optional images.
- Improve English documentation, issue templates, and repository organization.

## v1.0 — initial release

- GUI and CLI downloads, batch queues, progress, logs, and local previews.
- Parallel asset downloads, ICC project discovery, CYOAP Vue support, and
  website folder/ZIP output.
- Settings import/export, dependency diagnostics, and offline self-tests.
- URL and path validation, archive limits, atomic writes, and log redaction.
