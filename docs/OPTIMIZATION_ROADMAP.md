# Optimization roadmap

Updated November 15, 2026. Earlier stages are included in v1.1.5; the
checkpoint/index and GUI/startup batches below are unreleased.
See [performance measurements](PERFORMANCE.md) for fixture definitions,
comparative results, reproduction commands, and limitations.

The working estimate is about **92% of the planned optimization scope**. This
is a qualitative progress estimate, not a weighted milestone calculation or
an application-speed percentage. Preparing a release does not complete the
remaining performance work.

## Current status

| Area | Implemented | Remaining work |
| --- | --- | --- |
| Measurement | Local fixtures, byte/hash checks, process-tree RSS, real Tk/browser checks, repeated mapped-window/Tk-event startup measurements | Controlled OS-cold startup and broader real-world/low-spec hardware comparisons |
| Auto | Remove speculative classification probes; one-item GUI lookahead; small CYOAP pair reuse | Context-aware negative caching and finer discovery priorities |
| Media and output | File-backed standard media, cache handoff, streaming embedded JSON, incremental validation | Hard byte budget across decoders, legacy APIs, input JSON, and external backends |
| GUI | Bounded log/progress turns, chunked import, virtual queue, asynchronous panels, visibility-aware progress with unchanged-value reuse and paced animation | Large results pages, download snapshots, and remaining expensive callbacks |
| HTTP and retry | Shared 20-global/12-host admission; prioritized history; media-owned 429/5xx status retries | Adaptive admission and one retry budget across connection/body/auth/backend recovery |
| Browser transport | Dedicated owner thread, browser reuse, bounded page lifetime, file handoff | Broader lifecycle management and Selenium reuse |
| Runtime capture | Bounded small-response reuse; 50 ms wait slices; ready-gate noise filtering | Capture-tab rotation that preserves application state; hard RPC/body byte limits |
| History | Background metadata queue and eligible native source-header reuse | Streaming/browser/Auto-thread header provenance and interrupted-metadata resumption |
| Asset/index/resume | Mirror dependency graph, atomic asset/route metadata, verified explicit resume, eligible conditional validation, unchanged-file scan reuse, incremental queue metadata | Unify deep-scan/runtime/plugin provenance; broader catalog/result refresh and remote-update coverage |
| Distribution | Both layouts, frozen/runtime checks, selective test/foreign-helper data removal, measured warm startup and real CSV/XLSX/XLS imports | Controlled cold startup, further measured reductions while preserving optional features |
| Structure | Targeted service/controller extraction | Reduce global mutable state and legacy namespace synchronization incrementally |

## Checkpoint/index batch and remaining completeness work

The move from about 84% to about 90% reflects four completed parts of the
planned scope: mirror dependency identity/checkpoints, interrupted-route
continuation, repeated text-scan elimination, and incremental queue metadata.
It does not assign six percentage points of speed to these changes.

- The normal mirror's files remain the only payload store. A SQLite metadata
  index under `_audit` records exact requested/effective URLs, paths, MIME,
  size/hash, validators, and a versioned request-context digest. Transactions
  reject metadata from superseded generations. A normal run starts fresh;
  explicit Retry Assets continuation can reuse verified local snapshots.
- Completed route checkpoints retain authored outgoing links, redirect/base
  provenance and transitive asset hashes. They survive cancellation before the
  final manifest, and Retry Assets discovers those unfinished archives. Missing,
  edited, partial or context-incompatible files fall back to normal processing.
- Native, identity, unvaried, unredirected binary responses with valid ETag or
  Last-Modified can validate via conditional GET. A valid 304 avoids another
  body transfer. Missing/unsafe validators, changed cookies/profiles, browser
  state and transformed text use conservative fallbacks. Route snapshot resume
  does not assert that the remote site is still current.
- Text passes skip unchanged files only while source context and the completed
  URL/path mapping also match. Windows uses NTFS ChangeTime, not Python's
  creation-time `ctime`. Queue catalog refresh reuses unchanged checkpoint JSON;
  directory/stat work and full catalog output generation remain.

Still needed: unify metadata from generic deep scan, runtime capture, project
pipelines and plugins; broaden remote update/revalidation; profile large results
and other catalogs; and retain separate download, integrity and offline-viewer
outcomes. A successful checkpoint is not proof of every interactive story branch.

Acceptance: fewer duplicate scans/probes, exact output bytes/local references,
correct resume after interrupted writes, and no stale record overwriting a
new run. The earlier collection audit of 134 unique downloads, 106 passing
viewers, and 28 viewers with issues is historical; do not present it as a
current release result without rerunning that corpus.

## Remaining memory and network work

The move from about 90% to about 92% reflects verified idle/hidden GUI rendering
reductions plus a measured distribution/startup pass. It does not imply a
universal improvement in mouse, compositor or download latency. The developer
machine's heartbeat timings did not consistently improve; avoiding unnecessary
work is the demonstrated idle benefit. OS-cold testing remains incomplete.

- Establish a shared byte admission policy for large bodies/results across
  browser RPC, extractors, HTTP/2, decoder buffers, and concurrent workers.
  Declared Content-Length is not a trustworthy hard memory limit.
- Keep 64 KiB file transfers and existing file ownership/cancellation contracts.
  Do not truncate legitimate large assets just to meet a memory target.
- Coordinate total retry attempts across status, connection, body, authentication,
  and backend recovery without multiplying attempts. Honor Retry-After and
  preserve per-thread request policy isolation.
- Evaluate adaptive parallelism on slow/429/timeout fixtures. The current fixed
  HTTP limits intentionally trade unlimited-server throughput for bounded load.
- Treat cancellation response and transport shutdown separately. An already
  active browser or network API call can retain its own timeout even when the
  GUI has acknowledged cancellation.

Acceptance: count actual attempts, active leases, retained bytes, processes,
and partial files. Preserve successful output and unchanged-byte checks under
slow servers, malformed sizes, authentication changes, cancellation, and disk
failure.

## Browser and runtime lifecycle

Preserve delayed assets and safe interaction boundaries while reducing empty
waits. Capture pages must retain sessionStorage, cookies, window.name, and
navigation state across routes until a compatible rotation strategy is proven.
Small-response file quotas are admission policies, not total Chromium RSS caps.

Test staged controls, timer-loaded assets, long polls/noise, session-only URLs,
Unicode CSS/JS dependencies, cancellation during browser calls, and process
cleanup. Count avoided browser launches and requests rather than inferring
speed from fewer Python calls. Keep native and browser authentication contexts
isolated when origin/profile changes.

## Startup and packaging

Measure EXE launch to first window to input-ready separately from module import
time. Use cold and warm runs with process-tree RSS and a fixed feature set.
The historical single GUI import measurement of about 469 ms is not an EXE
startup baseline, and nested import times must not be added together.

Audit eager optional imports and PyInstaller data/hidden imports only after
profiling. Package size reductions must preserve promised modes and optional
integrations. Keep Tcl/Tk data/DLL checks and build from a complete staging
folder. Verify the extracted final release, not only the build work directory.
See [the release process](RELEASING.md).

## Incremental structural work

Extract queue controllers, maintenance jobs, browser owners, and storage services
at behavior boundaries with explicit job identity and cancellation context.
Reduce global mutable state and compatibility mirroring only after active
GUI/CLI/plugin bindings have regression coverage. File splitting and line-count
reductions alone are not performance improvements.

## Acceptance process

1. Preserve a trusted control and a reproducible fixture before changing a hot
   path. Change one dominant cause per experiment.
2. Measure at least five runs when practical; increase repetitions when results
   vary. Separate benchmark execution from builds and heavy tests.
3. Run focused tests, lint, and the full suite at integration points. Report
   skipped tests separately. Use opt-in GUI/browser smokes where applicable.
4. Verify byte/hash/reference equivalence, output modes, cancellation, resume,
   and failure reports. ZIP container hashes can differ due to metadata or
   compression; compare member bytes and CRC as well.
5. Include malformed/truncated bodies, false lengths, 429/503, missing sources,
   damaged cache, disk failure, dialog closure, and Cancel followed by a new run.
6. Keep profiling local, inexpensive, optional, and free of credentials. Archive
   raw logs and old local builds outside source control, retaining rollback
   artifacts until the versioned release has passed final checks.
