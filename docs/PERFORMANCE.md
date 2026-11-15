# Performance measurements and limits

This guide consolidates the November 14, 2026 optimization experiments included
in v1.1.5. Measurements describe specific fixtures on the development machine;
they are not internet-speed guarantees. The [roadmap](OPTIMIZATION_ROADMAP.md)
separates implemented work from remaining work.

The final optimization integration passed **1,511 tests**, with **12 skipped**,
and repository lint. The offline self-test passed **39/39**. Release checks are
repeated against the versioned Windows artifacts before publication. Optional
GUI/browser tests require a desktop and installed Playwright Chromium.

## Measurement conventions

- Use isolated profiles, temporary output, and local servers or synthetic data.
  Do not benchmark against your only copy of a downloaded collection.
- Compare old and new implementations under the same conditions. Report the
  median and number of runs. Separate pipeline RSS increments from total
  process-tree RSS, including browser and Node processes.
- Verify output bytes, SHA-256, local references, ZIP CRC, and cleanup outside
  the timed interval unless the measurement explicitly includes validation.
- Run benchmarks separately from builds and heavy tests. Disk/OS caches, startup,
  process scheduling, and browser revisions affect results.
- Keep raw audit output and control snapshots outside the repository. Control
  files passed to benchmark tools must be trusted source code. Historical
  machine-specific build paths and old binary hashes are not release assets.

## Cache and ZIP writes

Cache maintenance coalesces overlapping checks and serializes eviction. A scan
already in progress does not cover writes that arrive later. Capacity, LRU
order, hashes, explicit checks, and symlink/junction exclusions remain intact.

| Fixture | Before | After |
| --- | ---: | ---: |
| Cache: 12,000 files, 48 puts, six workers; median of three runs | 2.560 s / 48 scans | 0.855 s / 16 scans |
| ZIP: valid PNG and JSON, 15.83 MB; median of five paired runs | 0.284 s | 0.033 s |
| ZIP output size | 12,627,005 bytes | 12,623,160 bytes |

The cache experiment disables index persistence to isolate maintenance cost.
ZIP timing includes creation and built-in structure checks; additional CRC and
member-byte comparisons run outside the timer. Sampling chooses ZIP_STORED for
known media that gains little from compression; text and compressible content
retain DEFLATE. Results depend on asset composition.

```powershell
python tools/benchmark_cache_writes.py
python tools/benchmark_zip_compression.py
```

## Auto preparation

Auto classification checks CYOAP structure without probing 152 speculative ICC
filenames when those probes cannot change the website mode. Full discovery
still runs when archiving. The GUI resolves the current item first and prepares
at most one following item during its download, rather than blocking on the
whole batch. Manual and resume-skipped items do not trigger Auto probes.

Validated CYOAP JSON pairs can travel with `AutoDetection` when their combined
body is at most 256 KiB. Endpoint identity and original text are preserved;
failed, partial, or larger pairs use normal fetching. Results belong to a queue
snapshot position, so duplicate URLs remain separate jobs. Shutdown joins the
preparation worker before another run can reuse cancellation state.

| Fixture, five runs | Before | After |
| --- | ---: | ---: |
| Mock HTTP 404 site: initial classification requests | 153 | 1 |
| 12 Auto items, simulated 40 ms detector: time to first downloader call | 122.96 ms | 40.90 ms |
| Local HTTP 404 server, 20 ms header delay: median classification | 346.16 ms | 22.11 ms |

The local control reconstructs the former exhaustive classifier and alternates
paired run order. It uses direct Requests calls to exclude user proxy settings.
These are classification/dispatch measurements, not time to the first saved
media byte or a guarantee that every complete batch finishes sooner.

```powershell
python tools/benchmark_auto_preparation.py --local-http --output auto.json
python -m pytest -q tests/test_auto_preparation_optimization.py tests/test_auto_responsiveness.py
```

## File-backed media

Standard project media uses 64 KiB transfers to run-owned files while computing
SHA-256, size, and a 512-byte content prefix. URL aliases share file descriptors,
and deduplication uses the streaming hash. Persistent cache hits are verified
and copied to run-owned files so later eviction cannot remove an active source.
Commits use same-volume rename or chunked atomic copy, retaining prior files
after a failed copy.

Identity Content-Length is checked against decoded bytes; compressed wire sizes
are not compared with decoded sizes. Interrupted bodies follow existing retry
and failure reporting. Cancellation joins producers before removing spool and
partial files. Existing URL/path, audio, Discord, MediaWiki, and fallback rules
remain available.

| Synthetic transfer, four workers | Before | After |
| --- | ---: | ---: |
| 32 × 8 MiB: median RSS increment, five fresh processes | 257.00 MiB | 0.63 MiB |
| Same fixture: peak Python allocation | About 257 MiB | About 0.59 MiB |
| Same fixture: median pipeline time | 0.248 s | 0.214 s |
| 32 × 64 MiB = 2 GiB: median RSS increment, ten runs | Not measured | 0.59 MiB |
| 2 GiB fixture: median pipeline time | Not measured | 1.295 s |

All 32 files matched size and hash in every run. RSS was sampled every 10 ms;
tracemalloc measured Python allocations. These are additional fixture-process
bytes, not total application/browser RAM. The 2 GiB time ranged from 1.214 to
11.181 seconds, so it does not establish stable throughput. Every run stayed
below a 1 MiB RSS increment. Synthetic bodies measure transfer, not codec decode.

```powershell
python tools/benchmark_media_streaming.py --output media.json
python tools/benchmark_media_streaming.py --asset-mib 64 --runs 10 --output media-2gib.json
```

## Embedded JSON and both output

Standard embed/both output writes base64 directly to atomic UTF-8 JSON using
48 KiB input blocks and at most 64 KiB base64 characters per block. Both mode
uses the same file source for embedded data and local media. Existing string
APIs remain compatible; callers using those APIs still retain their output
string. Audio above the existing 10 MiB embed threshold remains external, and
both mode still saves its file. Existing data URIs are protected during URL
replacement, and image-credit suffixes do not become base64 bytes.

Final validation reads a bounded prefix of string values while checking every
escape/control character. The standard JSON decoder still validates structure
and scalar rules. Manager registration uses the read-only summary, retaining
full keys/version fields. This avoids a second full embedded-JSON read without
claiming constant RAM for arbitrary document structure or huge keys.

Three fresh processes per size/mode, four workers, synthetic bodies, cache off,
and warmed dependencies. Timing includes output and validation:

| Fixture | RSS increment before → after | Python allocation before → after | Median time before → after |
| --- | ---: | ---: | ---: |
| Embed, 16 × 8 MiB | 650.20 → 1.44 MiB | 821.46 → 0.56 MiB | 0.946 → 0.936 s |
| Both, 16 × 8 MiB | 650.42 → 1.41 MiB | 821.47 → 0.55 MiB | 1.106 → 0.945 s |
| Embed, 32 × 64 MiB | Large baseline omitted → 1.58 MiB | Not measured → 0.59 MiB | Not measured → 14.274 s |
| Both, 32 × 64 MiB | Large baseline omitted → 1.52 MiB | Not measured → 0.60 MiB | Not measured → 14.629 s |

The 2 GiB cases produce about 2.67 GiB of base64 plus JSON. Both also saves
2 GiB of original media. RSS increments stayed below 1.60 MiB across these runs.
The large buffered baseline was intentionally omitted to avoid excessive RAM.

A separate packaged-EXE comparison downloaded 16 × 8 MiB from loopback with
normal caching, fresh profiles, and three runs per mode. It includes startup,
download, output, both-mode ZIP, and validation; browser/yt-dlp/deep scan are off:

| Mode | Total process-tree peak RSS before → after | Median job time before → after |
| --- | ---: | ---: |
| Embed | 837.09 → 64.86 MiB | 7.152 → 7.064 s |
| Both | 847.89 → 65.28 MiB | 7.848 → 7.431 s |

Embedded JSON hashes and ZIP member bytes matched. Eighty compatibility
fixtures cover Unicode, spacing, escaped URLs, HTML, aliases, and query strings;
the intentional difference removes credit text from image data URIs. Two
hundred random JSON documents also exercise summary validation.

```powershell
python tools/benchmark_media_streaming.py --mode both --media-type image --assets 16 --runs 3 --stream-output --validate-output --output both.json
python tools/benchmark_embed_executable.py --before "old/CYOA Downloader.exe" --after "new/CYOA Downloader.exe" --report exe-memory.json
```

## Cache panels

Settings/Maintenance and Cache Center share one maintenance worker per GUI.
Statistics, limit persistence/eviction, and clearing run outside Tk; parsing,
confirmation, and widget changes stay on Tk. Duplicate disk jobs are rejected.
Closing a view discards late callbacks; closing the app waits through a 100 ms
poll instead of blocking Tk with join. A failed settings commit prevents eviction
and reports failure before controls become available for retry.

Real Tk, isolated profiles, five runs per path, and simulated 250 ms storage
callbacks. Save/Clear include a statistics read. No user cache is read or cleared.
Median worst 5 ms-heartbeat scheduling gap:

| Panel/action | Before | After |
| --- | ---: | ---: |
| Settings open | 266.0 ms | 19.8 ms |
| Settings Save | 504.3 ms | 7.8 ms |
| Settings Clear | 505.6 ms | 6.7 ms |
| Cache Center open | 282.6 ms | 32.9 ms |
| Cache Center Save | 502.0 ms | 7.1 ms |
| Cache Center Clear | 504.5 ms | 7.3 ms |
| Cache Center Refresh | 253.7 ms | 8.4 ms |

Action callbacks take about 1.3–2.0 ms to submit work. The worst new gap across
all final runs is 50.1 ms, versus 506.3 ms before; the highest scenario/run p95
is 22.1 ms. Panel construction stays on Tk. These figures describe isolated
callbacks and scheduling, not disk speed, screen repaint, or compositor latency.

```powershell
python tools/benchmark_cache_panel.py --expect-async --output cache-panel.json
python -m pytest -q tests/test_cache_panel_async.py
```

## Virtual queue and paged panels

The queue keeps its complete model but reuses widgets for the viewport and five
spare rows. The design allows up to 64 viewport/spare slots plus two pinned
editor/drag slots. Stable item IDs preserve filenames, modes, offscreen status,
reordering, duplicates, and snapshot results. Model commits use at most 256
items/6 ms per turn. Shared fonts, coalesced scroll updates, and cancelled timers
avoid repeated configuration and idle render loops.

Real Tk, fresh 1280 × 900 transparent windows, no network, synthetic model input,
five runs per completed configuration. Import includes model commit and initial
viewport rendering; CSV/Excel parsing is excluded:

| Items | Widget rows before → after | Median import before → after | RSS increment before → after |
| --- | ---: | ---: | ---: |
| 138 | 138 → 7 | 2.703 → 0.143 s | 24.92 → 1.11 MiB |
| 1,000 | 1,000 → 7 | 48.848 → 0.154 s | 181.41 → 1.69 MiB |
| 10,000 | Incomplete baseline → 7 | Incomplete baseline → 0.462 s | Full baseline not measured → 8.24 MiB |

The single 10,000-item baseline was stopped after 30 seconds with only 769 rows
created, so its partial memory/time is not presented as a full baseline. The
harness also has a 700 MiB additional-memory guard. All new runs completed.
At 1,000 items, median worst heartbeat gap drops 227.9 → 25.2 ms and median p95
223.4 → 20.2 ms. The worst new gap across all sizes is about 28.7 ms. Total peak
GUI RSS at 10,000 items is about 89.32 MiB. Model RAM still grows with item count.

Library/history read SQLite/JSON outside Tk and display at most **12 cards per
page**, built one per Tk turn. Search debounces for 120 ms. Selection survives
pages/search; **Select All Matches** covers every matching page. Bulk queue
imports commit in bounded batches. Local Serve preparation runs in a worker,
and starts its server only if the requesting panel is still alive.

History coalesces progress and retains errors/filter state. Closing its panel
cancels queued probes without blocking Tk; active requests retain their 10-second
timeout and close their own responses. Errors includes both error/unreachable.

Real Tk, 1,000 synthetic records, three fresh processes/profiles per side,
simulated 250 ms storage reads, immediate network results, and warmed GUI:

| Operation | Callback median before → after | Median worst heartbeat gap before → after |
| --- | ---: | ---: |
| Open library | 4,230 → 23 ms | 9,811 → 37 ms |
| Search library, all records match | 6,583 → less than 1 ms | 8,279 → 44 ms |
| Open history | 278 → 27 ms | 5,981 → 44 ms |
| Filter history to Updated | 4,443 → 31 ms | 4,461 → 32 ms |

Search callback timing measures debounce scheduling; heartbeat includes rendering.
Page-ready times, including a 200 ms harness tail, are about 803/649/693/448 ms,
versus 14.2/14.9/10.5/8.2 seconds. Storage reads on Tk drop from six to zero;
1,000 cards, or 500 filtered cards, become at most 12. The worst new heartbeat
gap across runs is about 122 ms, including the more expensive first opening.
These figures do not measure EXE startup, real database/network speed, or RAM.

```powershell
python tools/benchmark_queue_virtualization.py --items 10000 --output queue.json
python tools/smoke_virtual_queue.py --report queue-smoke.json
python tools/benchmark_library_history.py --records 1000 --runs 3 --expect-async --output panels.json
python tools/smoke_library_history.py --report panels-smoke.json
```

## Shared HTTP admission and retry ownership

Native sessions, cookie/cloudscraper sessions, and HTTP/2 deep scans share
**20 active leases per process / 12 per normalized hostname**, across ports and
schemes. A streaming response keeps its lease through body consumption or close.
Redirects, transport/body errors, partial generators, and finalizers release
leases without taking another host's capacity while waiting.

Admission orders by priority then FIFO: foreground requests use priority 10 and
history uses 20. Waiters check cancellation every 50 ms. Active transports retain
their existing timeout/retry behavior. Mutable session configuration is not made
universally thread-safe by admission.

Three fresh processes/profiles per side, four executors/sessions, two loopback
hosts, 100 ms server delay, SHA-256-verified bytes. The limited fixture sends 429
above 12 active requests per host:

| Fixture | Completed/actual requests before → after | Peak active before → after | Median time before → after |
| --- | ---: | ---: | ---: |
| Healthy, eight workers | 64/64 → 64/64 | 8 → 8 | 0.880 → 0.868 s |
| Healthy, 32 workers | 96/96 → 96/96 | 32 → 20 | 0.348 → 0.541 s |
| Host limit 12, 32 workers | 96/112 → 96/96 | 24 → 20 | 2.141 → 0.554 s |

The limited fixture removes 16 extra 429 responses; new per-host peaks never
exceed 12. An unlimited healthy server can be slower because admission limits
parallelism. This is overload control, not a universal throughput increase.

The media loop owns 429/500/502/503/504 retries in its native request scope,
preventing multiplication by adapter retries. Direct images keep four attempts;
other requests retain their former policy. Connection/read retries remain with
the transport. Retry-After supports seconds and HTTP dates; waiting is
cancellable and closes responses first. Cookie recovery is reserved for 401/403.

Five local HTTP runs per scenario, real Requests/urllib3, recorded waits instead
of actual sleeping for repeatability:

| Direct-image scenario | Requests before → after | Scheduled waits before → after |
| --- | ---: | ---: |
| Persistent 429, Retry-After 2, configured wait 2 | 16 → 4 | 30 → 6 s |
| Persistent 503, Retry-After 2, fixture backoff 0 | 16 → 4 | 24 → 6 s |
| 429/503 recovers on request four | 4 → 4 | 6 → 6 s |

These are scheduled waits, not measured wall-clock durations. Separate CLI
smokes use real waits. A total four-request bound is not claimed across every
fallback backend, authentication profile, connection error, and body retry.

```powershell
python tools/benchmark_network_budget.py --runs 3 --output network.json
python tools/benchmark_status_retries.py --output retries.json
python tools/smoke_status_retries.py --report retries-smoke.json
```

## Browser ownership

Each `BrowserFetchSession` owns one thread and one active operation. Runtime,
browser/context/page creation, navigation, body access, spooling, and cleanup
stay on that owner. Chromium is reused; origin/session/cookie/agent changes
replace context, and proxy changes restart the browser. Matching HTTP cookies
and agent are transferred without leaking other-origin browser storage.

Pages rotate after **eight responses or 32 MiB** of completed body data. This
is a lifetime policy checked after a body completes, not a hard RSS cap or an
asset truncation limit. Body completion uses navigation timing before retrieving
server bytes. Files are handed to media/website callers, and response close owns
reader/spool cleanup. API calls to content can still load a full body.

Admission/result waits check cancellation every 50 ms; active navigation keeps
its 30/45-second API timeout. Shutdown joins owner cleanup before spool removal.
Selenium compatibility fallback is serialized but still starts a driver per
call. Frozen builds look for the exact matching external Chromium revision;
bundled browsers and explicit browser paths take precedence.

Three fresh process/profile runs per side, real headless Chromium, local padded
PNG bodies, full SHA-256 checks, and whole Python/Node/Chromium-tree RSS:

| Fixture | Launches before → after | Peak browsers before → after | Median time before → after | Peak RSS before → after |
| --- | ---: | ---: | ---: | ---: |
| Fallback, 12 × 4 MiB, four workers | 12 → 1 | 4 → 1 | 92.540 → 12.181 s | 1,060.45 → 532.92 MiB |
| Existing website session, 8 × 1 MiB, one caller | 1 → 1 | 1 → 1 | 8.203 → 8.227 s | About 373.9 → 369.1 MiB |

The website control uses one caller to respect the old API's thread affinity;
separate tests verify concurrent new callers. Every fixture-owned browser/driver
must exit. Browser transfer still receives one complete body through Playwright
RPC before spooling. Browser cache, codecs, DOM, and that body have no universal
byte cap. Request admission above does not cover Chromium's internal network.

```powershell
python tools/benchmark_browser_reuse.py --output browser.json
python tools/benchmark_browser_reuse.py --transport session --assets 8 --asset-mib 1 --output session.json
python tools/smoke_browser_reuse.py --report browser-smoke.json
```

## Gallery file handoff

Gallery-dl output passes through a 64 KiB file copy into an atomic `FilePayload`
with size, SHA-256, and a 512-byte prefix. Cache, deduplication, folder/ZIP, and
embed/both consume the file without a second complete byte buffer. Extractor
scratch is removed before handoff; disk/read errors fail the job instead of
silently trying another browser. The old one-argument byte API stays available.

Five isolated processes per side, eight synthetic extractor files × 64 MiB,
four workers, cache off, actual helper/pipeline, and all file hashes verified:

| Measurement | Before | After |
| --- | ---: | ---: |
| Median total process peak RSS | 299.28 MiB | 43.74 MiB |
| RSS increment | 257.41 MiB | 1.61 MiB |
| Peak Python allocation | About 256.44 MiB | About 0.77 MiB |
| Median helper plus pipeline time | 0.586 s | 0.647 s |

Time increases slightly; this reduces large buffers. The benchmark generates
extractor files locally and does not measure real gallery-dl process RAM or
remote gallery compatibility. A separate CLI fixture passes embed/ZIP/both,
verifying two 8 MiB assets per mode and cleanup across six extractor processes.

```powershell
python tools/benchmark_media_streaming.py --gallery-file-fixture --media-type image --assets 8 --asset-mib 64 --workers 4 --runs 5 --output gallery.json
python tools/smoke_gallery_handoff.py --report gallery-smoke.json
```

## Runtime response reuse and waits

Completed browser GET 200 responses can be spooled and passed through the normal
mirror path instead of downloaded again. Eligibility requires identity encoding,
no Content-Range, and positive declared Content-Length at most **2 MiB**. The
capture store admits at most **32 MiB / 128 entries**, with at most 128 pending
response handles. Length, URL/query identity, SSRF, VPN, cancellation, CSS/JS
processing, and cleanup are preserved. Larger/compressed/unknown/partial bodies
keep the normal downloader; no asset is truncated to meet the store quota.

Five fresh processes/profiles per side, real Chromium, 12 × 256 KiB assets,
25 ms server delay, native throttle disabled, and exact output hashes:

| Median measurement | Before | After |
| --- | ---: | ---: |
| Server asset requests | 24 | 12 |
| Asset-body transfer | 6 MiB | 3 MiB |
| Capture plus saving | 8.877 s | 8.656 s |
| Python/Node/Chromium peak RSS | 349.21 MiB | 368.08 MiB |

Peak RSS rises about 19 MiB; file admission quotas are not hard RSS limits,
especially for false size headers or browser RPC buffering. The benefit is
avoiding duplicate requests and retaining browser-session bytes.

Capture/scroll waits now pump events, drain finished responses, and check
cancellation in at most **50 ms slices**. Original initial/scroll budgets stay
intact for delayed assets. Known archive noise does not block a ready safe
control; ordinary pending API/assets still do. A changed page, 300 ms stability,
and a following safe control are still required before advancing early. Final
gates retain the full budget. Request tracking resets per route while page/context
state stays intact across routes; there is no capture-tab rotation yet.

Five fresh processes/profiles per side, two sequential pages with three gates
each, six 64 KiB assets, persistent telemetry noise, verified sessionStorage and
file hashes, original 1,800 ms initial budget:

| Median measurement | Before | After |
| --- | ---: | ---: |
| Capture plus saving | 24.077 s | 19.288 s |
| Asset requests / verified bytes | 6 / 384 KiB | 6 / 384 KiB |
| Python/Node/Chromium peak RSS | 347.75 MiB | 346.54 MiB |

The roughly 20% time reduction belongs to this noise-blocked ready-control
fixture. A separate three-run cancellation fixture sends Cancel 200 ms after
document arrival during initial settling and includes browser/store cleanup:
median **1.672 → 0.109 seconds**, range **1.656–1.672 → 0.094–0.469 seconds**.
Navigation, locator clicks, evaluate, body RPC, and browser close retain their
own timeouts; 50 ms slices are not a universal cancellation bound.

```powershell
python tools/benchmark_runtime_reuse.py --runs 5 --output runtime.json
python tools/benchmark_runtime_reuse.py --progress-gates --pages 2 --assets 6 --asset-kib 64 --noise --runs 5 --output waits.json
python tools/smoke_runtime_reuse.py --report runtime-smoke.json
python tools/smoke_runtime_waits.py --report waits-smoke.json
python tools/smoke_runtime_waits.py --cancel-only --runs 3 --report cancel.json
```

## History metadata

The active GUI worker persists successful history before optional metadata HTTP
work. One daemon worker allows **32 pending jobs plus one active job**, coalesces
the same URL/history path, and uses HTTP priority 20. Record tokens prevent stale
results from overwriting new success/failure records or restoring removed entries.
The path is captured, commits are atomic, and retention stays at 1,000 entries.

Each probe has a separate cancellation event; clearing the GUI event for another
run does not revive old probes. Session/header/cookie/proxy/DNS/environment
fingerprints guard results without persisting credentials. Ineligible results,
full queues, and cancellation produce explicit unavailable/skipped/cancelled
states. Pending or unavailable baselines are reported as Errors by update checks,
not silently marked current. Daemon work can remain pending on process exit;
automatic metadata resumption after restart is not implemented.

Five local HTTP runs, four successful entries, 150 ms header delay:

| Median measurement | Before | After |
| --- | ---: | ---: |
| Four history calls return | 0.648 s | 0.007 s |
| All metadata complete | 0.648 s | 0.639 s |
| Extra Range/identity probes | 4 | 4 |

This removes network work from the queue's blocking path; total work is similar.
Disk and lock contention can still affect history persistence.

The GUI can also reuse three bounded header values from a completed native
source GET. URL/query, request/final URL, session, and profile must match from
job start through capture and consumption. The snapshot retains no response or
body, is consumed once, and is cleared before another source fetch or after a
failed download. Each value is limited to 4,096 characters.

Only completed GET 200 identity bodies qualify. Streaming, browser/cloudscraper,
redirect, Range/partial, conditional, Vary, Set-Cookie, no-store, compressed,
truncated, oversized-header, and changed-context responses keep background probes.
Unknown length requires another usable validator. Metadata from a different
project.json or asset is not substituted for the entry URL. The original history
API stays synchronous by default; CLI downloads normally do not write history.

Five local HTTP runs per side/case, four GUI entries, 100 ms header delay. The
real GUI worker/native HTTP are exercised; archive/UI boundaries use a harness.
The history control and optimized side use the same harness/capture overhead:

| Median measurement | Before | After |
| --- | ---: | ---: |
| Eligible identity total requests / extra probes | 8 / 4 | 4 / 0 |
| Identity queue return | 0.424 s | 0.444 s |
| Identity all metadata complete | 0.535 s | 0.444 s |
| Gzip total requests / probes | 8 / 4 | 8 / 4 |
| Gzip all metadata complete | 0.562 s | 0.575 s |
| Vary total requests / probes | 8 / 4 | 8 / 4 |
| Vary all metadata complete | 0.543 s | 0.543 s |

Every final baseline matches identity size and ETag, including a different wire
ETag for gzip. Eligible request counts halve, but queue timing does not improve
in these samples because probes were already asynchronous. HTTP integration
tests confirm current baselines become updated when the server ETag changes.

```powershell
python tools/benchmark_history_metadata.py --background --runs 5 --output history.json
python tools/benchmark_history_header_reuse.py --runs 5 --output headers.json
python -m pytest -q tests/test_history_metadata_background.py tests/test_history_response_headers.py
```

## Validation and remaining limits

Each optimization stage passed the full available suite and appropriate focused
checks. The final source integration contains 1,511 passing tests, 12 default
skips, 39 passing self-test cases, and clean lint. Earlier validation counts are
historical, not additional tests to add to that total. Queue interactions passed
12/12 source/Tk checks, library/history 9/9, browser-owner source smokes 4/4, and
runtime response/wait source checks 3/3 for each focused group.

Portable integration builds passed 15 general checks from staging and final
locations, including diagnostics, nine output modes, transient HTTP 503 recovery,
ZIP CRC, routes, iframe/XHTML/SVG/PDF, and redirected CSS dependencies. Focused
frozen browser and delayed-runtime checks also passed. Source/Tk measurements do
not establish native packaged-GUI latency; packaged smokes exercise CLI/output.
Frozen module comparison, complete runtime hashes, and Tcl/Tk checks guard the
Windows distribution. See the [release process](RELEASING.md).

Temporary disk use grows when RAM buffers move to files. Input JSON/model/key
memory, full-body legacy APIs, codecs, browser RPC/cache, third-party extractors,
and independent backends still need memory. The process-wide HTTP budget does
not cover all browser/extractor requests. No universal byte budget or repeated
cold/warm first-window startup result is claimed.
The [roadmap](OPTIMIZATION_ROADMAP.md) tracks these remaining areas.

## Unreleased: mirror checkpoints and incremental scans

The checkpoint-stage integration passed **1,563 tests**, with **12 skipped**, **40/40**
offline self-test cases, clean Ruff checks and all 15 public Markdown checks.
These are stage-specific totals; the latest GUI/startup integration is below.

Five paired runs against a trusted copy of the pre-change mirror, alternating
execution order, isolated temporary files, and no simultaneous build/heavy tests:

| Fixture | Before | After |
| --- | ---: | ---: |
| 120 JavaScript bundles, six localization passes, median | 2.422 s | 0.451 s |
| Text reads over those passes | 720 | 120 |
| 600 queue checkpoint records, 20 metadata refreshes, median | 0.659 s | 0.612 s |
| JSON reads over those refreshes | 12,000 | 600 |
| Explicit resume: 16 × 1 MiB native PNGs, median | 0.240 s | 0.293 s |
| Resume GETs / repeated body bytes | 16 / 16 MiB | 16 / 0 bytes |

All 120 text-file SHA-256 hashes remain identical. The resume fixture verifies
every asset's size/SHA-256 and ZIP member bytes/CRC outside timing. Its server
delays headers by 10 ms and supplies an identity representation with an ETag.
The new side receives sixteen 304s. Resume timing includes local verification
and metadata work, and is slower in this final sample. An earlier five-pair
sample was faster (0.313 → 0.237 s), so no stable resume-time gain is claimed.
Avoided body transfers and unchanged-file scans are the demonstrated benefits.
Catalog timing covers metadata selection, not complete HTML/CSV publication.

The mirror's `_downloaded` map and dependency graph identify requested/effective
URLs and existing files. Atomic SQLite transactions persist metadata in `_audit`
after file commit, with size/hash, content type, validators and a versioned
context digest. No additional payload cache is introduced. Normal downloads
start a new generation. Explicit resume checks hashes once per unchanged file
identity within that run; metadata writes from old generations are rejected.
Windows ChangeTime detects edits that restore LastWriteTime and file size.

Route records preserve raw outgoing links, redirect/base provenance and
transitive dependency hashes, allowing interrupted traversal to continue before
the final manifest exists. Retry Assets finds these incomplete archives. A
completed route is a local snapshot, not evidence of remote freshness or full
interactive correctness. Legacy archives without checkpoints keep the previous
manifest recovery policy. Deep-scan/runtime/plugin provenance is not yet unified.

Conditional reuse requires an exact URL including query bytes, unchanged
profile/cookies/settings, a verified file and a native GET 200 identity response
without redirect, Vary, Set-Cookie, Content-Range or no-store. Only untransformed
assets qualify. ETag takes precedence over Last-Modified. Missing/invalid
validators, transformed text and browser state use normal fetching. Invalid 304
metadata, a changed file/context during validation or an unexpected 304 without
a representation also trigger a full fetch; a second unusable 304 fails safely.

Text scan reuse depends on file identity, remote source and the completed
URL/path mapping. Files modified during analysis are not admitted. Metadata and
modernization backup directories are excluded. Queue metadata snapshots belong
to the supervisor; changed/deleted files invalidate entries. Both paths still
perform filesystem checks and do not provide a universal memory bound.

```powershell
python tools/benchmark_archive_index.py --control-source path/to/trusted-old-website.py --runs 5 --output index.json
python tools/benchmark_checkpoint_resume.py --control-source path/to/trusted-old-website.py --runs 5 --output resume.json
python -m pytest -q tests/test_archive_checkpoints.py tests/test_website_archive_optimization.py tests/test_live_library_layout.py
```

## Unreleased: idle GUI rendering and Windows startup

Two indeterminate detail bars previously started during construction and kept
animating while the app was idle or the detail panel was hidden. A 10-second
profile recorded 636 progress redraws and 58,028 Tk bridge calls in an idle GUI.
This is unnecessary work even though no file is being downloaded.

The bars now start quiet. Telemetry is still collected while details are hidden,
but only visible presentation changes cross the Tk bridge. Opening details or
restoring the main window renders the latest snapshot. Visible unknown-length
bars share one 80 ms timer using public CTk stepping APIs. Collapse, unmap,
rebuild and shutdown stop that timer; generations reject late callbacks. Stable
text, bar values/configuration and graph signatures avoid redundant redraws.

Five paired fresh processes, alternating order, real mapped transparent Tk
windows at 1100 × 720, isolated profiles, no downloads. Each row is a median
over a two-second scenario, after 250 ms of layout settling:

| Scenario | Tk bridge calls before → after | Progress redraws before → after |
| --- | ---: | ---: |
| Idle, details collapsed | 11,695 → 290 | 128 → 0 |
| Idle, details expanded | 11,691 → 296 | 128 → 0 |
| Active, details collapsed | 15,061 → 346 | 167 → 0 |
| Active, details expanded | 15,241 → 4,101 | 169 → 42 |
| Active, window unmapped | 15,065 → 348 | 167 → 0 |
| Completed | 291 → 290 | 0 → 0 |

These counts establish less wasted rendering, not a universal speed percentage.
The 10 ms-heartbeat p95 did not consistently improve on this machine (idle
12.0 → 16.6 ms); Windows timer scheduling remains visible. Process CPU accounting
has about 15.6 ms granularity here, so zero-valued short samples do not mean zero
CPU usage. This is not a physical mouse/compositor latency or low-end-laptop
measurement.

Windows bundles now omit only identified pandas development-test data and the
two foreign-platform Selenium Manager executables. Public testing helpers,
spreadsheet engines, native Selenium Manager, Playwright/Node, gallery-dl,
yt-dlp/EJS, Tcl/Tk and other runtime resources remain. Selection is applied both
before analysis and after hooks populate final data/binary tables.
The pruning audit accounts for all 1,124 omitted files (26,531,733 uncompressed
bytes). Every retained runtime member matches the control by SHA-256, except
ZIP-container metadata differences whose member bytes and CRC match exactly.

| Package measurement | Instrumented control | Updated candidate |
| --- | ---: | ---: |
| Standalone EXE bytes | 132,160,781 | 122,907,125 |
| Embedded runtime data/binary members | 6,641 | 5,517 |
| Portable ZIP extracted files | 6,642 | 5,518 |

Startup controls use the preceding application plus the same opt-in phase
probe. The candidate also includes the GUI changes above. Six alternating
launches per side/layout use a fresh process/profile each time: the first
observed launch is recorded separately and five subsequent launches provide
the medians below. Benchmarks run without concurrent builds/heavy tests.

| Repeated-launch median | Control | Candidate |
| --- | ---: | ---: |
| Standalone: process launch → script entry | 5.120 s | 4.521 s |
| Standalone: process launch → main-window Map | 6.741 s | 6.189 s |
| Standalone: process launch → delivered Tk event | 6.745 s | 6.193 s |
| Portable: process launch → main-window Map | 1.534 s | 1.531 s |
| Portable: process launch → delivered Tk event | 1.538 s | 1.534 s |

First observed event-ready launches were 7.545 → 7.450 s for standalone and
4.420 → 3.225 s for portable. OS caches and antivirus state are uncontrolled:
these are not proven OS-cold timings. Script-entry time includes bootloader,
extraction, interpreter and runtime-hook work. The readiness probe observes a
Tk event delivered after main-window mapping and UI construction; it does not
measure the first physical click or compositor paint. Peak process-tree RSS
was about 97 MiB standalone / 88 MiB portable and did not materially decrease.
Portable avoids the standalone layout's per-launch extraction cost.

The integration passes **1,602 tests**, **12 default skips**, **40/40** offline
self-tests, clean lint/Markdown checks and **3 opt-in real-Tk GUI checks**.
Additional tests cover hidden/unmapped views, latest-state restoration, shared
animation ownership, unchanged widgets, replacement widgets and stale timers
after Cancel/New Run. Package verification compares all 139 frozen application
modules and the main script with source and validates Tcl/Tk/SQLite/runtime
bytes. CSV, XLSX and generated BIFF8/CFB XLS fixtures exercise real frozen
readers with Unicode names and exact embedded-image bytes; the retained native
Selenium helper is exercised offline.

```powershell
python tools/benchmark_idle_gui.py --control-root path/to/trusted-checkout --runs 5 --seconds 2 --output idle.json
python tools/benchmark_startup.py --before path/to/instrumented-control.exe --after path/to/candidate.exe --runs 6 --output startup.json
python tools/smoke_batch_formats.py --executable "path/to/CYOA Downloader.exe" --runtime-root path/to/portable/_internal --report batch.json
```

## Unreleased follow-up: sidebar mouse work and direct workspace construction

The preceding idle-animation fix did not remove another cost encountered while
moving the mouse over an otherwise idle GUI. Each child widget's Leave binding
reselected the current mode, repainted all mode rows and refreshed the mode
description, which reread settings. Crossing a row's icon, title and description
repeated that work without a selection change.

Hover now paints only the affected row. Widget identity and palette/options
participate in unchanged-style reuse. Explicit clicks and settings/language/theme
refreshes still update the selection and description. Startup paints the default
selection and directly constructs the current progress workspace; it no longer
builds the obsolete three-card workspace only to destroy it immediately. Panel
discovery uses the log's stable main container instead of the former host depth.

Five alternating paired fresh processes against commit `9a2e3df`, isolated
profiles, real transparent mapped Tk at 1100 × 720, no downloads or concurrent
builds/heavy tests. A two-second scenario dispatches actual Enter/Leave events
across the row, icon, text frame, title and description, scheduling the next
crossing 20 ms after each callback completes. These are synthetic Tk crossings,
not physical mouse/compositor timings. Medians of each run's measurements:

| Measurement | Control | Follow-up |
| --- | ---: | ---: |
| Hover Enter/Leave callback p95 | 31.02 ms | 5.23 ms |
| Tk bridge calls per crossing | 4,738 | 722 |
| Settings reads during hover | 33 | 0 |
| Crossings completed in the scenario | 33 | 65 |
| 10 ms heartbeat p95 during hover | 42.26 ms | 10.95 ms |
| Quiet idle heartbeat p95 | 16.07 ms | 16.37 ms |
| GUI constructor | 733.37 ms | 716.55 ms |
| Progress bars created / immediately discarded | 7 / 3 | 4 / 0 |

The timer is completion-paced, so faster callbacks execute more crossings in
the same time. Normalize work per crossing rather than comparing raw totals as
if the event counts were equal. The extended harness also runs a mouse timer
outside the hover scenario; its Tk counts are not directly comparable with the
earlier harness's idle counts. Quiet-idle timing did not improve further.
The small constructor difference does not establish a substantial startup win.
Native Tk widget/font creation remains significant: one diagnostic run spent
about 222 ms creating the first emoji label. A diagnostic 500 ms constructor
target was not met; it is not a portable CI threshold or a claimed result.

The live presentation regression fails on discarded startup bars and on real
mouse Leave events that refresh descriptions/read settings/repaint unrelated
rows. It also verifies selected/unselected hover colors, actual click behavior
and the exact input/queue panel ancestry. Run this in a fresh process with
`CYOA_GUI_SMOKE=1`; the Windows workflow includes it explicitly:

This follow-up passes **1,602 source tests**, with **15 default skips**,
**40/40 self-tests**, and **6 opt-in real-GUI checks** run in separate processes
(the three new presentation checks and the three existing layout/performance
checks). Lint, compilation and repository/documentation checks also pass.

```powershell
python -m pytest tests/test_gui_presentation_work.py -q
python tools/benchmark_idle_gui.py --control-root path/to/trusted-checkout --runs 5 --seconds 2 --output gui-followup.json
```

## Unreleased batch: reports and generated-image scan work

Reports Center formerly constructed every result card synchronously and created
three fonts per row. It now freezes membership of the published append-only job
records, prepares the report/groups in a window-owned worker, and builds at most
12 cards one at a time with three shared row fonts. Filters retain all matching
records; page controls expose the rest. CSV always exports the complete snapshot,
independently of the visible page/filter. Serialization, disk writes and failure
text formatting run off Tk. CSV uses a temporary file and atomic replacement;
cancellation or write failure before commit preserves an existing export.
Closing a window cancels its task and suppresses late UI delivery.

The BFS deep scanner now retains generated-image class/group/name metadata
rather than every previously scanned source body. It processes each new script
unit once and reuses unchanged inferred paths. A local LRU holds at most 8,192
canonical URLs for that scan only, preserving the existing query normalization
and keeping separate runs independent. Mandatory-character checks reject clear
negative inputs before expensive regex passes. Constructor extraction no longer
runs a full-document search separately for every Entry class.

The compatibility inference API keeps joined-source semantics when a positive
contract may span source fragments, including split class bodies and constructor
calls. Proven negatives avoid joining text. Overlapping literal matches,
Unicode case-insensitive class matching, repeated class definitions and group
replacement remain covered by tests.

Five alternating paired fresh processes against commit `7e4ad75`. Profiles and
files are isolated, no downloads occur, and benchmarks run separately from
tests/builds. The report fixture uses real mapped transparent Tk with 500 mixed
job results and no global modal input grab. Readiness below means that the
selected page has been built and its preparation task has completed.

| Report fixture, median | Control | Candidate |
| --- | ---: | ---: |
| Open callback | 1,469.52 ms | 94.21 ms |
| Initial cards built | 500 | 12 |
| Fonts added during open callback | 1,512 | 19 |
| Widget/page build readiness | 1,491.72 ms | 506.16 ms |
| Failed-filter callback | 2,328.54 ms | 41.89 ms |
| Failed-filter build readiness | 2,330.08 ms | 308.82 ms |
| 10 ms heartbeat p95 during open | 1,408.87 ms | 27.67 ms |
| 10 ms heartbeat p95 during filter | 1,533.10 ms | 14.38 ms |

All 500 results remain in the report model; the new readiness measurement is for
the first page, while the control builds all cards. These figures measure Tk
scheduling/widget construction, not physical input/compositor latency or EXE
startup. Timings vary with Windows scheduling and layout; counts are the more
direct evidence of bounded widget work. Models still grow with result count,
and final clipboard delivery remains a Tk operation.

| Backend fixture, median | Control | Candidate |
| --- | ---: | ---: |
| Scan metadata pass, 24 files / 26,880,000 bytes | 90.99 ms | 20.14 ms |
| Same pass: peak Python allocation | 51.52 MiB | 3.33 MiB |
| Same pass: canonical URL parses | 750 | 30 |
| Compatibility inference, 80 classes / 80 exact generated paths | 1,542.50 ms | 40.15 ms |
| Same positive-contract inference: peak Python allocation | 3.97 MiB | 3.87 MiB |

The metadata-pass fixture supplies 30 already-known URLs through the scanner
plugin interface to isolate canonicalization and generated-image metadata work.
It excludes plugin discovery, HTTP, cache writes and retries. SHA-256 checks
confirm all 24 input files remain unchanged. The positive-inference fixture has
one padded source plus 80 Entry class/constructor sources; every expected path
matches. Its compatibility join remains, so no material allocation reduction
is claimed for that case.

Allocation peaks come from tracemalloc enabled on both sides and exclude fixture
creation/imports; they are not total process/browser RAM. Plugins still receive
whole source strings, metadata grows with recognized declarations/literals, and
one huge source can still be large. The per-scan URL cache is bounded by entries,
not bytes. This batch does not establish a global RAM limit or internet-speed
improvement. Existing output layout, naming and asset bytes remain unchanged.

Validation includes source/real-GUI tests, cancellation during preparation and
CSV export, preserving old exports on failure, full filtered-report exports,
cross-file metadata in later BFS rounds, URL-cache lifetime and Unicode/fragment
compatibility. The offline self-test now includes
`incremental generated image inference` for checking the frozen binaries.

The integration passes **1,616 source tests**, with **20 default skips**,
**41/41 offline self-tests**, and **11 opt-in real-GUI checks** in separate
processes (five reports, three presentation, three existing layout/performance).
Lint, compilation and repository/documentation checks also pass.

```powershell
python tools/benchmark_bulk_optimization.py --control-root path/to/trusted-checkout --runs 5 --records 500 --output bulk.json
python -m pytest tests/test_generated_image_index.py tests/test_deep_scan_dependency_optimization.py tests/test_report_io.py -q
```
