# Changelog

User-facing changes are listed by release. Current setup and troubleshooting
instructions are in [README.md](README.md) and the linked guides.

## Unreleased

- Stop hidden/idle progress animations, draw changing progress only when visible,
  and use one paced animation timer for visible unknown-length transfers. Restore
  current telemetry when details or the window become visible again.
- Measure GUI startup through main-window mapping and a delivered Tk event.
  Omit pandas development-test data and Selenium helpers for other platforms
  from Windows packages while retaining native helpers and runtime integrations.
- Add atomic mirror asset/route checkpoints and verified interrupted-archive
  continuation through Retry Assets, including recovery before a final manifest
  exists. Reject changed hashes, profiles/cookies and superseded metadata writers.
- Validate eligible native binary assets with conditional GET on explicit resume;
  preserve complete URL queries and keep full-fetch fallbacks for unsafe metadata.
- Skip unchanged text localization passes and reuse unchanged queue checkpoint
  metadata during catalog refresh. Keep the readable `Title/index.html` layout.
- Add paired scan/resume benchmarks and extracted-package source/runtime checks.

## v1.1.5 — November 15, 2026

- Store real-queue viewers directly as `Title/index.html`, keeping internal IDs,
  staging, and process logs in `_audit`. Preserve authored title tags, use numeric
  suffixes for duplicate names, and provide a recoverable organizer for older
  `Title [ID]/archive` outputs without downloading assets again.
- Consolidate performance reports into an English guide and a current roadmap.
  Keep generated downloads, audit output, caches, and Windows build artifacts
  outside the source distribution. Publish both standalone and portable builds.
- Reuse completed native source-response headers in successful GUI history
  records, avoiding a second metadata request when URL/query, session, and
  profile match. Keep one small header snapshot per job, discard stale or
  superseded snapshots, and retain background probes for compressed, varied,
  redirected, streaming, conditional, partial, or otherwise ineligible responses.
- Persist successful GUI download history before optional network metadata
  collection. Run probes on one bounded background worker, coalesce repeated
  URLs, discard superseded/profile-changed/cancelled results, and retain the
  synchronous history API. Report incomplete metadata baselines explicitly
  during update checks instead of marking them current.
- Poll runtime settling/scroll waits in cancellable 50 ms slices and drain
  completed responses during waits. Keep delayed-asset budgets, exclude known
  archive noise from ready-gate pending checks, scope request tracking to each
  route, and release browser references before native mirroring.
- Reuse completed small runtime GET responses as run-scoped temporary files
  instead of fetching them again. Keep URL/query identity, normal CSS/JS and
  safety checks, fallback for large/compressed/partial/unknown-size bodies,
  cleanup on cancellation, and captured/reused counters in the archive manifest.
  Release browser transport between archive phases without disabling later recovery.
- Pass gallery-dl output to standard project media jobs as hashed temporary
  files in 64 KiB chunks instead of reading a complete asset into memory.
  Preserve the byte API and older plugin callables, clean extractor scratch,
  and propagate job spool failures without starting another backend.
- Let the project media loop own 429/500/502/503/504 status retries, avoiding
  multiplication by shared HTTP adapter retries. Keep transport connection/read
  retry behavior and thread isolation, honor Retry-After dates for media errors,
  report the last HTTP status, and extract browser cookies only after auth errors.
- Reuse a job-owned browser transport on one dedicated thread for project
  fallback and website fetches. Serialize browser body transfer to files,
  rotate pages after eight responses or 32 MiB, and isolate contexts when the
  origin, HTTP cookie/session/agent, or proxy changes. Stream temporary browser
  files through website responses and remove them on close. Cancel admission
  promptly, discard late results, and join the owner before cleaning its files.
- Stream embedded JSON and base64 directly to atomic UTF-8 output for standard
  embed/both jobs. Spool media in all project modes, reuse committed media in
  both mode, retain the string-returning API, and clean temporary files on
  cancellation or disk failure. Validate JSON and register Manager projects
  without loading complete base64 bodies. Preserve existing data URIs and keep
  image credit text out of generated base64 values.
- Share HTTP admission across independent sessions/executors: at most 20 active
  leases globally and 12 per hostname, including streamed bodies, cookie and
  cloudscraper sessions, and HTTP/2 deep scan. Prioritize waiting downloads over
  history checks, cancel queued admission promptly, and release slots on body
  completion, close, transport failure, or abandoned-response cleanup. Close
  retryable error bodies before asset backoff and Discord URL refresh.
- Read library/history data and prepare local Manager viewers off Tk. Page
  large lists with at most 12 cards, debounce library search, preserve selection
  across pages/filters, and queue bulk selections in bounded commits. Coalesce
  history progress, cancel queued probes on panel close, discard stale results,
  keep failure messages visible, and include unreachable entries in Errors.
- Virtualize the editable queue with a bounded row pool. Keep filenames,
  modes, drag/reorder, remove, retry highlights, and offscreen progress tied
  to item IDs, and prepare imports in bounded model batches. Pin an active
  editor/drag target during row reuse, share fonts, scale row geometry, and
  stop pending render timers on close. Snapshot job order so queue edits
  during a download cannot move a status update onto a different item.
- Move cache statistics, clearing, and limit persistence/eviction off Tk's
  owner thread in both Settings and Cache Center. Share one maintenance task,
  disable duplicate actions, detach destroyed views, and wait asynchronously
  for an active disk transaction when closing the app. Report a failed limit
  commit before eviction instead of showing a successful save.
- Stream project image/audio bodies to run-owned files for folder/ZIP output.
  Compute hashes during transfer, reuse them for image deduplication, read/write
  persistent cache files in chunks, and commit the first output by rename.
  Preserve buffered embed/both behavior and existing authored-path rules.
  Validate decoded body lengths and clean partial files after cancellation,
  interrupted transfers, or failed disk writes; join producers before cleanup.
- Start Auto queue downloads after resolving the current item, with just one
  following item prepared in the background. Preserve row identities, resume,
  cancellation, and CYOA.CAFE custom-viewer handling. Remove speculative ICC
  filename probes from mode selection and reuse small validated CYOAP JSON
  pairs during archiving without retaining failed or partial probes.
- Measure real Tk scheduling and log-presentation latency under offline bursts.
  Batch colored log inserts within a short time budget, drain progress counters
  promptly, and coalesce widget redraws while preserving lifecycle updates and
  the final pending frame. Keep idle polling and queue memory bounded.
- Parse queue lists off-thread, build rows in short GUI turns, and share queue
  fonts. Prevent partial imports from starting/exporting, cancel pending rows
  on Clear/close, and restore controls after parser failures or cancellation.
- Yield GUI progress work within a short time budget under heavy callback load.
  Advance stable opening gates without a full fixed pause, retain settling for
  delayed/final content, and skip repeat settling when no scrolling occurred.
  Avoid the CYOA.CAFE shell probe on unrelated hosts during project discovery.
- Exercise real CSV queues with isolated workers, resumable checkpoints, duplicate
  URL removal, offline-browser reports, and a local catalogue that serves each
  viewer at its own HTTP root.
- Archive tiled image-overlay pages, conditional language data scripts, project
  custom CSS dependencies, HJSON application data, and public Perchance HTML
  application manifests. Ignore Vite virtual module keys during file discovery.
- Keep localized application-bundle media URLs valid in both DOM and module
  contexts when the offline viewer serves the archive at its own HTTP root.
- Follow explicit original-image links on MediaWiki File pages before browser
  fallback. Avoid duplicate request throttling and VPN discovery for CLI logs
  when the VPN guard is disabled.
- Recognize image URLs fused to credit text and filenames containing apostrophes.
  Successful plain image fields become usable local paths while source-project
  backups retain the original credits.
- Keep source-tooltip attribution links out of image/audio download queues,
  while retaining explicit inline badge images. This avoids hundreds of wiki,
  social-post, and gallery-page browser fallbacks on large Plus 2 projects.
- Handle staged opening buttons and changed labels, share solved HTTP-session
  cookies with Chromium, tolerate cookie decryption failures, and supply missing
  collection defaults when older Plus 2 projects use a newer offline viewer.
- Keep original and redirected source URLs while rescanning saved CDN assets.
  Resolve CSS imports and image/font paths against their stylesheet origin,
  preserving protocol-relative URLs and repeated authored directory names.
- Reuse one folder traversal per deep-scan round for both candidate discovery
  and the disk index. Skip modernization backups, protect recovery files from
  active references, and check cancellation between scanned files.
- Supply existing successful asset URLs and source mappings to deep scan so
  relocated files keep their dependency context without repeated downloads.
- Tokenize responsive image candidates once across scanning, localization, and
  integrity checks. Preserve data URLs, commas in image queries, candidate
  order and descriptors, including separators without following spaces.
- Eliminate repeated copies of the remaining `srcset` input and verify all
  responsive attributes, including `data-srcset`, without false missing-file
  reports for inline image payloads.
- Keep deep-scan filenames literal through saving and cache registration.
  Reserve registered output paths to prevent later asset overwrites and
  reject invalid cache paths without normalizing them into unrelated files.
- Archive same-origin iframe documents and their assets without overwriting the
  entry page. Reuse active documents for cycles and bound nested HTML to 16
  levels; retain cross-origin and `srcdoc` frame markup.
- Preserve SVG fragments and encode literal asset filenames in HTML, CSS and
  JavaScript links. Handle quoted CSS filenames with spaces or parentheses,
  SVG `href`/`xlink:href`, and redirected CSS/JavaScript dependency bases.
- Skip JavaScript path inference in CSS, including inline styles. Check SVG
  and object dependencies during integrity validation, and release responses
  and waiting workers when asset path allocation fails.
- Reuse the archived entry source during Smart/Browser crawling and skip
  repeated audio-player scans of unchanged scripts. Resolve page assets and
  navigation against the final HTTP redirect URL.
- Preserve literal manifest filenames, original route link bases, and queued
  routes across resume. Retry failed or deleted pages without reinterpreting
  existing offline links. Reject non-HTML responses as navigable routes.
- Commit mirrored HTML and patched audio scripts atomically, and observe
  cancellation while waiting for another worker's asset download.
- Add an optional portable folder build (`-Layout onedir`) to avoid one-file
  runtime extraction at each launch, with the same modules and integrations.
- Add an offline packaged-EXE check covering diagnostics, all nine output modes,
  ZIP checksums, embedded images, and recovery from transient HTTP 503 responses.
- Bound outstanding asset futures to twice the configured worker count across
  project images, deep scans, JavaScript preload lists, and CYOAP downloads.
  Poll cancellation while workers are blocked and cancel queued work on exit.
- Reuse directory-enumeration metadata during cache limit checks while keeping
  the same size limit, LRU eviction order, and symlink/junction exclusions.
- Defer GUI compatibility composition for CLI information commands. Preserve
  imported facade symbols and initialize the complete patch stack for GUI use.
- Make HTTP retry/backoff and `Retry-After` waits cancellable. Read one consistent
  network settings snapshot during GUI worker setup.
- Honor Unicode BOMs in local TXT/CSV queues and contain damaged Excel reader
  failures. Reject ambiguous TXT exports without overwriting existing files.
- Report excessive JSON nesting and oversized numeric settings as validation
  failures. Check decoded project asset references containing Unicode, spaces,
  escaped separators, and query strings.
- Preserve literal itch.io HTML5 ZIP filenames and decode CYOAP asset URLs once.
  Keep escaped URL delimiters in asset filenames and skip malformed scan values.
- Stop cancelled HTTP redirect chains and avoid starting cancelled file streams.

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
