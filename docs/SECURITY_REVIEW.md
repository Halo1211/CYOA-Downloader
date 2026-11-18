# Security review: Python baseline

Source: Sol's independent static review, 2026-11-18. Claude checked the citations marked ✔ against the source. Nothing was run, so exploitability was not tested. Paths are relative to `cyoa_downloader_app/`.

| ID | Severity | Status | Finding | Location | Rust requirement |
|---|---|---|---|---|---|
| SEC-1 | HIGH | Confirmed from source ✔ | The browser backend calls `page.goto()` with no internal-host or SSRF interception. A public page can make the browser request loopback or RFC1918 addresses, which bypasses the native-fetch policy | `network/browser_owner.py:157` ✔; `network/runtime_capture.py:486`; compare `download/website.py:1543` | Apply one destination policy everywhere: navigation, redirects, subresources, workers, and helper executables |
| SEC-2 | MEDIUM | Confirmed (needs links) ✔ | The preview server (bound to 127.0.0.1, random port ✔) inherits `SimpleHTTPRequestHandler`. It has no resolved-root containment, keeps directory listing on, and sends `Access-Control-Allow-Origin: *` ✔. Symlinks or junctions can expose files outside the folder | `gui/app.py:11936-12131` ✔ | Check the canonicalized root for every route, reject reparse-point escapes, turn off directory listing, restrict CORS |
| SEC-3 | MEDIUM | Confirmed ✔ | A Discord token stored in the legacy keyring is copied into plaintext `settings.json` on the next save (intentional compatibility code). Plain API-key modes also exist | `config/settings.py:371-391` ✔ | Keep secrets in the keyring by default. Plaintext only by explicit user choice. **Owner decision D5** |
| SEC-4 | MEDIUM | Confirmed (Sol) | Some paths have no byte ceiling: the HTTP/2 deep scan buffers whole bodies, the streaming writer has no max size, browser bodies are fully read, and RAR helper stdout is fully captured | `download/image_pipeline.py:1542`, `download/package.py:718`, `network/browser_owner.py:172` ✔, `offline_viewers/injector.py:70` | Enforce incremental byte, file, job, disk and time budgets |
| SEC-5 | INFO | Hypothesis | ZIP validation is strong (traversal, device names, case collisions, links, expansion are all handled), but the early metadata cap can be skipped for some ZIP64 records and for already-open archives | `core/archive.py:42,61,111`; `core/paths.py:107` ✔ | Port the rules, then fuzz them with `proptest` |
| SEC-6 | INFO | Suspected | DNS rebinding window between the policy check and the connection | `integrations/ai_core.py:326` | Resolve once, then connect to the vetted IP |
| SEC-7 | LOW | Confirmed ✔ | Chromium is launched with `--no-sandbox` | `network/browser_owner.py:113` ✔ | Do not pass `--no-sandbox` |
| — | INFO | Confirmed | No `verify=False` and no `shell=True` found ✔. Subprocesses use argument arrays. Filenames are sanitized for reserved Windows names ✔ | — | Keep it that way |

No CRITICAL findings.

## Policy carried into Rust

- Never disable TLS verification.
- Treat downloaded HTML, JSON and agent output as untrusted data.
- Do not commit secrets.
- Preview content must never reach privileged app IPC.

## Session 4 — Sol review of output stage (run/output/reports/cli)
9 findings; fixed by Claude (Luna writes failed):
- High: no-clobber publication (owned temp + hard_link/create_new; concurrent writers tested).
- High: exclusive run temp dir with RAII cleanup (also on future abort).
- Medium: pre-existing `.part` files are never deleted; unique pid+counter temp names.
- Medium: ZIP entry names validated (no `..`, absolute, drive, backslash).
- Medium: filenames over 200 bytes bounded at char boundaries (normal names unchanged → Python parity).
- Medium: output stage runs in spawn_blocking with cancellation before each publication.
- Medium: CLI keeps the real result if Ctrl-C registration fails.
- Open (deferred): aggregate run byte budget (KI-R5).

## Session 4 — Sol review of GUI worker (concurrency)
Fixed by Claude:
- High: cancel/shutdown no longer go through the bounded job channel; shared `Control` state means they are never lost.
- Medium: a panicked job task now emits Finished(Failed).
- Medium: tokens are dropped when jobs finish.
- Medium: cancelled queued jobs finish immediately.
- Medium: pending queue capped at 10,000.
- Medium: the folder dialog runs on its own thread.
- Medium: close waits at most 500 ms, with a bounded drain.
- Low: the UI is woken only when an event is actually queued.
- Open: the UI job history grows with user-submitted jobs (virtualized rendering; no pruning yet).

## Session 5 — Sol review of website mirror (W1–W3)
14 findings. Fixed by Claude (Luna could not write files):
- **High:** multibyte URL panic in `repair_scheme` (byte-wise prefix checks).
- **High:** allocated paths were percent-decoded twice at write time (`a%252Fb` vs `a/b` collision). Writes now use `mirror::contained_path`: literal components, traversal rejected, link/junction ancestors rejected.
- **High:** `index.html` is now reserved like Python `__init__`.
- **High:** CSS/JS dependency chains are bounded (`MAX_DEPENDENCY_DEPTH` 64, mirrors Python RecursionError → "request failed").
- **High:** HTML nesting deeper than 512 is rejected (parity exception: bs4 serializes iteratively).
- **Medium:** site budgets (100k requests, 20 GiB).
- **Medium:** checked numeric-range arithmetic. Starts beyond u128 are skipped (parity exception).
- **Medium:** array scan honours its 100 kB window.
- **Medium:** per-host DNS classification is cached.
- **Medium:** the entry redirect URL is used as the page URL.
- **Medium:** the custom-viewer route is published before downloads.
- **Medium:** the active-page mapping is cleared on every exit; the page is recorded only after its write.
- **Medium:** casefold collision identity.
- **Perf bug found while testing:** the tokenizer cloned the whole document per tag, and fancy-regex unbounded searches rescanned the rest of the document. Now linear: 10k nested tags 5.4 s → 8 ms.
- Open (same as Python): local TOCTOU ancestor swap between containment check and write.
- `project.json` shared output across origins (Python behaviour).

## Session 6 — F-017 custom DNS (Sol review, task 20261118-175757-sol-b4b317dfe8)
Reviewed commit 1ed0c4f (`cyoa-net` dns.rs, dns_resolve.rs). Findings and resolution:
- MEDIUM: replies not bound to the question → FIXED: `accept()` requires 1 question, standard opcode, identical QNAME (case-insensitive)/QTYPE/QCLASS. Tests: `accept_binds_answer_to_question`.
- MEDIUM: DoH allowed HTTPS→HTTP redirect (plaintext query / proxy escape) → FIXED: DoH client `https_only(true)`, redirects disabled. Test asserts plain-HTTP endpoint receives nothing.
- MEDIUM: Windows platform verifier blocks synchronously beyond deadline → FIXED: DoT exchange runs on a blocking thread with its own timeout; verification unchanged.
- LOW: system fallback had no app deadline → FIXED: 20 s deadline (OS thread may outlive it).
- LOW: trailing-dot normalisation → FIXED (strip all, like Python). IDNA: Rust uses UTS-46 for non-ASCII labels, Python IDNA 2003 — DOCUMENTED difference (e.g. `faß.de`).
- Confirmed OK by Sol: DoT keeps cert+hostname verification; UDP source/ID checks; bounded TCP/DoH/cache; no packet-triggered panic; UDP/TCP/DoT bypass HTTP proxies like Python.
- OPEN: wiring into `PolicyResolver` must keep the internal-address (SSRF) rejection for custom, cached and fallback answers (DNS-3).

## Session 6: CYOA Manager integration (Sol task 20261118-211907-sol-0456c946a9)

Read-only review of `cyoa_integrations::cyoa_manager` at 2ec56d7. No SQL injection (bound parameters; column list from a fixed whitelist).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High (conditional: attacker can write in the output dir) | Extraction could write through a link planted between operations | Fixed: `create_new` files (never opens an existing path or link) and a canonical containment check of each parent. A fully race-free extraction needs handle-relative no-follow I/O; residual race documented (Python has the same window) |
| 2 | Medium | Validation and extraction reopened the ZIP; extraction skipped reserved names, trailing dots, duplicates, size/ratio checks | Fixed: one handle is hashed, preflighted and validated (`cyoa_storage::archive::open_validated_zip`) and the same `ZipArchive` is extracted; member names go through `safe_archive_rel_path` |
| 3 | Medium | Reused folder could supply a linked/FIFO `project.json` | Fixed: only regular, non-link files are accepted; the marker must be a regular file. Manifest verification of reused folders not added (Python also trusts the marker) |
| 4 | Medium | Probe buffered whole top-level strings | Fixed: unrelated keys are skipped with `IgnoredAny`; only `rows`/`version`/`styling` are inspected (test with a 400 KB field) |
| 5 | Low | Ignored strings skipped UTF-8 validation | Fixed: `Utf8Checked` reader rejects invalid UTF-8 anywhere, including a code point truncated at EOF |
| – | Parity | `version` digits: Python `isdigit` accepts non-ASCII digits | Kept ASCII-only; documented in FEATURE_PARITY |

## Session 6: deep scan + ICC mode (Sol task 20261119-000914-sol-26c8670d0a)

Static review of 4daffc4 (deep_driver.rs, deep_scan.rs, icc.rs, MirrorSink::download_site). No traversal write found; scheme/userinfo rejection confirmed in FetchClient (redirects included); block_on placement inside spawn_blocking confirmed correct.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | One task per candidate, all bodies held until the batch ended; deep scan bypassed request/byte budgets | Fixed: batches of workers×2 saved before the next batch; caps 20 000 candidates/round, 100 000 requests and 20 GiB per run |
| 2 | High | Cross-port SSRF: the resolver exemption is per hostname, so an internally resolving page host on another port/scheme was reachable | Fixed: `resolves_internal_cross_origin` blocks any (scheme, host, port) different from the page origin whose host resolves internal, matching Python `_ssrf_block_cross_origin`; unit test |
| 3 | Medium | Pre-project scan hard-coded 4 workers | Fixed: `MirrorSink.workers` from the run's worker setting |
| 4 | Medium | Post-project scan walked/parsed files on an async worker | Fixed: runs on a blocking thread driving the async fetches via the runtime handle |
| 5 | Medium | Tile-count overflow with huge values (Python: 1 tile) | Fixed: overflow-safe ceiling division; unit test |

## Sol review 2026-11-19 (cafe, cafe_static, cyoap, pure, routes, profile)

Fixed: CYOAP cancellation no longer finishes as success (typed cancel -> RunError::Cancelled, no report/ZIP); CYOAP node list deduplicated and capped (20k), site-asset BFS capped (20k requests).
Fixed later: cafe stale-cache invalidation (Python parity); route-crawl redirects leaving scope are recorded as failed and not archived (deliberate divergence from Python; the redirect request itself is still made by the client).
Open: aggregate byte budgets for CYOAP/static gallery; small per-file writes in cyoap still run on the async worker (ZIP/finalize moved to spawn_blocking); cafe resolver lacks Python's JSON/CSS-viewer validation branches; cafe_static creates parent dirs before containment check (junction case).
