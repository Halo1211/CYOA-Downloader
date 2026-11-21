# Overnight report (updated continuously)

> Historical log of sessions 1–4. Current state: `docs/MIGRATION_PROGRESS.md`.

Last update: 2026-11-18 ~02:30. Branch `migration/python-to-rust`. Nothing has been pushed.

## Executive summary

- Phases 0–2 are done: environment, audit and architecture.
- Stage 0 (workspace), Stage 1a (paths/url), Stage 1b (atomic I/O, ZIP validation) and Stage 1c (settings, secrets) are implemented, tested against Python golden fixtures, and committed.
- **Remaining:** Stage 2 (cache, history, resume, checkpoints) through Stage 10. Most of the application (network, download engine, parsers, GUI, integrations) is **not started**. Realistic estimate: well under 10% of the total port is done.

## Technical decisions

See `DECISION_LOG.md` (DL-1…DL-9).
- GUI: egui/eframe.
- Workspace `rust/` with 8 crates.
- All features kept, with Rust replacements.
- Secrets in the keyring by default.
- Golden fixtures generated from live Python.

## Agent activity

| Agent | Tasks | Outcome |
|---|---|---|
| Claude | Audit synthesis, docs, gates, fixture generation, fixes | Fixed: golden-path normalization, ZIP symlink detection (zip `unix_mode` vs raw `external_attributes`), `python_repr` escaping, a wrong test expectation, keyring features, fixture EOL handling. **Mistake:** truncated `archive.rs` to 0 bytes with a failed `Set-Content`; recovered via Luna and Claude. Rule since then: edits only through the Edit tool |
| Luna | Stage 0, 1a, 1a fix, 1a security fixes, 1b, 1b recovery, 1c (2 attempts) | 0/1a/1b: good. Recurring sandbox problems: no crates.io access (worked around with `--offline`), no Python, intermittent "Access is denied" on file writes, one timeout. 1c failed twice and was escalated |
| Sol | Audit review, Stage 1a security review, Stage 1c implementation (escalation) | High value: found 2 HIGH path-containment bugs that the golden tests missed. 1c: 39/40 tests passed; one handwritten expectation was wrong (8 vs 6 spaces), corrected against Python |
| MiMo | 1 advisory task | Failed (repetition, false claims) |
| Qwen | 1 classification task | Low quality |

## Verification (actual)

- Python baseline: 1633 passed / 21 skipped.
- Rust: `cargo clippy --all-targets -- -D warnings` exit 0; `cargo test` exit 0, **50 tests** (cyoa-core 7, cyoa-storage 43). Golden cases: 221 path/url, 9 ZIP verdicts, 3 JSON byte files, 30 settings.
- Not run yet: cargo audit, cargo deny, llvm-cov, benchmarks.

## Commits (local)

c8705cc docs · e5c1d13 Stage 0 · e8ed523 1a · ffd7866 1a security fixes · ec49d01 1b · 33baf9d fixture EOL · 358aed2 1c · be02514 2a history/resume · c01bc67 2b cache · 66d88cd 2c checkpoints

## Update — session 2 ("lanjut"), checkpoint

- **`cyoa-parse::assets`** (commit dabc5f1):
  - `IMAGE_FIELDS`/`AUDIO_FIELDS`, YouTube/SoundCloud classifiers, srcset parsing, `extract_image_references`.
  - 80 Python cases identical.
  - Fix found during verification: `is_image_reference` must use Python `urlparse().path` semantics, including `;params` and `splitext`. Exposed as `py_urlparse_path`.
- **Totals:** 96 Rust tests pass; clippy clean.
- **Next (unchanged order):**
  1. `asset_scan._scan_large_json_for_assets` and `_deep_scan_project_assets` (lines 223–311, 1262–1315).
  2. Phase B regex collection in `_process_images` (lines 345–405); use `cyoa_parse::assets` and `cyoa_engine::embed`.
  3. Fetch loop (`fetch_one`) on `cyoa_net::client` + `cyoa_storage::cache`; MIME via `_media_content_extension`.
  4. Network discovery, outputs, CLI, end-to-end fixture test.

## Update ~06:30 — FINAL CHECKPOINT of this session (Claude context nearly exhausted)

- **`cyoa-engine::embed`** (commit 35a3166):
  - Text-level port of `core/embed_io.project_chunks`, byte-identical to Python on 6 cases: 200 KB base64 media, `https:/` variants, aliases, preserved data URIs, whitespace.
  - Also ports `_classify_project_image_references`.
  - Luna's Value-walking draft was rejected: it re-serialized the JSON and could never be byte-identical.
- **Totals:**
  - 95 Rust tests pass; clippy -D warnings clean; `cargo audit` shows 0 vulnerabilities.
  - 19 local commits on `migration/python-to-rust`. Nothing pushed.
- **Next session, in order:**
  1. **Image pipeline** (`download/image_pipeline.py`):
     - Collect image refs, download via `cyoa_net::client` into `cyoa_storage::cache`.
     - Detect MIME and build the mapping for `embed::project_chunks`.
     - Work in pieces: one function group per agent call.
  2. **Network discovery** (`discover.py` 510–860): `try_project_candidate`, `get_project_source`.
  3. **Outputs and reports:** output naming, `failed_images.txt` / `backup_report.txt`, ZIP/Both packaging (`download/package.py`).
  4. **`cyoa-cli` with clap:** first end-to-end parity test against a local Python `http.server` fixture site, comparing output bytes Python vs Rust.
  5. **Sol security review** of cache.rs and archive_checkpoint.rs (pending).

## Update ~06:00 — Stage 4 started (parsing and discovery)

- **`cyoa-parse::project`** (`parse.py`):
  - 18 texts × 5 functions identical to Python, plus brace extraction and decoding.
  - Intentional improvements: invalid `Infinity` becomes `null`; chardet misdetections replaced by correct decoding.
- **`cyoa-parse::discover`** (pure helpers from `discover.py` lines 1–510):
  - Candidate URLs, script sources (iterative `app.*.js` via fetch closure), HTML hints, default project candidates for 8 URL shapes (GitHub Pages, Neocities, itch, cyoa.cafe...): all identical to Python.
  - Python's backreference regex is replaced by an exact manual scanner.
  - Intentional difference: an unclosed `<iframe>` is parsed per the HTML5 spec.
- **Workspace:** 94 tests pass; clippy clean. Commit 7197cbe.

### Remaining Stage 4 work (in order)

1. **`discover.rs` network part:** `try_project_candidate`, `get_project_source` (lines 510–860) in cyoa-engine, using `cyoa_net::client`.
2. **Image references in the project JSON:** `orchestrator._classify_project_image_references`, `core/embed_io.py` (`project_chunks`, `EmbeddedMedia`).
3. **Embedded JSON writer and ZIP package** (`download/package.py`), output naming, reports (`backup_report.txt`, `failed_images.txt`).
4. **CLI MVP** (`cyoa-cli`, clap): `url -o out`, `--zip`, `--both`. Compare byte output with Python against a local `http.server` fixture site (deterministic, no live internet).

### How to continue (for a fresh session)

- **Agents:** Luna and Sol can no longer write files (sandbox "Access is denied"). Ask them with `luna_review` / `sol_review` to RETURN code (one file, under about 14k characters). Claude saves the file, wires the modules, runs `python fixtures/export_*.py`, and runs the gates.
- **Crates:** Claude pre-fetches every new crate (`cargo add` + `cargo fetch`) because agents build `--offline`.
- **Gates:** run from `rust/`: `cargo fmt --all -- --check`, `cargo clippy --all-targets --offline -- -D warnings`, `cargo test --offline`.

## Update ~05:15 — HTTP client done

- **`cyoa-net::client`** (Sol design; commit 7137978):
  - reqwest with a policy-enforcing DNS resolver, so every connection's resolved addresses are vetted.
  - Manual redirects with a per-hop policy check, Authorization/Cookie stripped cross-origin, and the admission lease moved to the new host.
  - Streaming download to a same-dir temp file with incremental byte cap (SEC-4) and atomic rename.
  - Cancellation and deadline on every await.
- **wiremock tests:** redirect chain/cap, internal-host redirect blocked, Authorization stripped across origins, oversize leaves no temp, mid-body cancel cleans up, lease released.
- **Test fix:** NTFS directory enumeration reports stale sizes for open files; the observer now queries the path.
- **Workspace:** 90 tests pass; clippy -D warnings clean.
- **`cargo audit`:** 0 vulnerabilities. 3 "unmaintained" warnings (zbus/zvariant) come only through keyring's Linux-target store and are not compiled on Windows.
- **Still open in cyoa-net:** proxy configuration (manual/env/per-scheme/bypass), custom DNS (hickory: UDP/TCP/DoH/DoT), VPN guard, response metadata, Cloudflare/FlareSolverr, charset-aware text decoding.

## Update ~04:30 — Stage 3a/3b-policy done

- **cyoa-net** has:
  - `admission` (20/12 fair async budget; Sol design; 7 tests).
  - `retry` (transport/media retry ownership, Retry-After, domain backoff; Sol).
  - `throttle` (Python window accounting, cancellable).
  - `policy` (one destination policy; CPython 3.12 address classification; DNS pinning API).
  - `headers` (CDN hotlink profiles).
- **Parity fixtures from live Python** (all pass): 43 host classifications, 8 SSRF decisions, 15 header profiles with key order.
- **Sol finding (confirms SEC-6):** Python rejects any internal DNS answer but then connects after a *second* resolution. The Rust policy returns vetted addresses for pinning.
- **Sol's replies truncate at ~15k characters.** Ask for one or two files per call.
- **Next:**
  - HTTP client on reqwest with a custom DNS resolver that enforces `DestinationPolicy` on every connection (initial and redirects), manual redirects, streamed bodies with byte caps (SEC-4), proxy.
  - Then DNS (hickory), the VPN guard, and response metadata.

## Update 03:20 — Stage 2 done

- **Working pattern change:** Luna's sandbox now denies file writes even for new files. Luna therefore works through `luna_review` and returns code as text; Claude saves it, fixes compile errors and adds Python fixtures.
- **Luna's drafts needed real corrections:**
  - Cache dropped the Python link/junction guards and had borrow errors.
  - The checkpoint DB name used `canonicalize()` (`\\?\` prefix), which would never match Python's `path_for`. Rewritten and proven by a fixture.
- **Parity proof:** Rust computes byte-identical checkpoint DB names, payloads, history/resume files and job keys to Python.
- **Deliberate difference:** checkpoint `get` always re-hashes. It cannot read NTFS ChangeTime without `unsafe`.
- **Tests:** 66 Rust tests passing.

## Blockers / owner attention

- **Luna sandbox:** no network and no Python, plus intermittent write denials. Workaround: Claude pre-fetches crates and runs the Python exporters.
- **casefold:** ZIP duplicate detection uses Rust `to_lowercase` where Python uses `casefold()`. They differ for characters like ß; tracked as a parity gap.
- **Settings gaps (Sol):** arbitrary-size integers, non-finite numbers, unpaired surrogates and some numeric-string forms are not matched exactly.
- **Native keyring:** not tested against Python's real Credential Manager entries. Needs a manual check on this machine before release.

## Next actions

1. Stage 2: cache index, history, resume, SQLite checkpoints (needs `rusqlite` pre-fetched).
2. Stage 3: network (reqwest, admission 20/12, retry) with a Sol review.
3. Stage 4: project discovery and embed/ZIP pipeline, plus CLI MVP (first end-to-end parity milestone).

## Update — session 3 (2026-11-18)

- **Report correction:** items 1–2 of the session-2 Next list were already committed (937f807 deep scan, 669fc73 `_process_images` collection, 3070429 asset naming). Verified baseline: 100 tests, fmt and clippy clean.
- **Agent bridge:** `team_status` OK (ChatGPT login; writes enabled). luna_review and sol_review OK. luna_implement can **create** files but gets "Access is denied" when **rewriting** them (rustfmt, edits). Rule: Luna drafts new files; Claude formats, fixes and integrates.
- **`cyoa-engine::fetch_plan`** (Luna draft, Claude fixes): the pure pre-network part of `fetch_one`. It covers asset URL resolution (CPython `urljoin` plus the `https:/` repair), `_ssrf_block_cross_origin` with injected DNS, and the direct-attempt count. 177 Python cases (95 resolve, 50 ssrf, 32 attempts) via `fixtures/export_fetch_plan.py`.
  - Bug fixed: `urljoin(base, "")` must return the base verbatim, including the fragment.
  - Luna's wrong unit expectation for `HTTPS:/` was corrected to the Python value.
- **Totals:** 106 Rust tests pass; fmt and clippy clean.
- **Next:** async fetch loop. Cache get, then `asset_is_error_document` rejection, then `cyoa_net::client` GET with headers, retry and byte cap, then cache put, then MIME/extension, tested against a mocked HTTP server. After that: gallery-dl, headless and cookie fallbacks (Sol), then network discovery, outputs, CLI, and an end-to-end test.

## Update — session 4 (2026-11-18)

- **Agent bridge:** Luna `luna_implement` now creates AND modifies files (temp-file test passed, file removed). Luna works directly again; Claude reviews diffs and runs gates.
- **Batch C1 `cyoa-engine::fetch`** (Luna): `asset_is_error_document`, async `fetch_one` direct-HTTP loop (cache get/put with error-doc rejection, SSRF screen, MediaRetryPolicy 429/5xx backoff, 404/410 permanent, 401/403 auth stop, Content-Type mismatch, cancel-aware sleeps); `FetchClient::bytes` (capped). 8 wiremock tests.
- **Known gaps (KNOWN_ISSUES):** no transport-level retries inside the Rust client (Python retries connect failures 3x inside fetch_response, Rust stops after one); other-4xx/TLS error strings differ from requests' wording; domain jitter fixed at 0.5; gallery-dl, headless, cookie session, Discord refresh, MediaWiki and VPN guard are DEFERRED.
- **Totals:** 114 tests pass; fmt and clippy clean.
- **Next:** C2: bounded-concurrency `process_images` driver (fetch_one over the collected assets, local naming via `localize`, writes, failed list), then E: embed writer/ZIP/reports, then CLI.
- **Batch C2 `cyoa-engine::pipeline`** (Luna; Sol review → 8 findings, 7 fixed): `process_images` with alias grouping, bounded workers, per-completion progress over bounded channel (try_send), panic surfacing, cancellation checkpoints, symlink/junction containment, no-clobber commits, spawn_blocking writes, site-folder reuse and dedup guard, failed_images.txt. Luna's first call timed out mid-work (files kept, verified). Claude fixed 2 clippy lints. 126 tests pass.
- **Batch D `cyoa-engine::discover_net`** (Luna): static-HTTP `get_project_source` (direct JSON, HTML hints/scripts/iframes/default candidates, depth limit), 7 wiremock tests. Luna reported "checks passed" but the file contained a literal `\r\n` that commented out a constant and broke the build; Claude fixed it. **Lesson: never trust agent-reported gate results; always rerun.** Deferred: cyoa.cafe catalog, browser/Cloudflare/cookies, AI detection, archive extraction. 133 tests pass.
- **Batch E + CLI MVP — FIRST END-TO-END PARITY** (Luna draft; Luna timed out/garbled once, then lost write access again → Claude finished):
  - `cyoa-engine::output` (naming, no-clobber paths, atomic JSON, deterministic ZIP), `run` (shared CLI/GUI backend: discover → process_images → outputs), `reports` (`_metadata.json`, `failed_assets.txt`, local timestamps), `cyoa-cli` (clap: url/-u/-o/-z/-b/-t/-w, Ctrl-C → 130).
  - Bugs fixed by Claude: Windows `sync_all` on read-only handle → "Access is denied"; output stem used project URL instead of user URL; Luna's guessed naming expectations; output.rs duplicated an unverified `clean_url_path_component` (now reuses localize's); subdomain logic now follows tldextract/PSL (ICANN-only) via `psl` crate.
  - **Proof:** `bash scripts/e2e_parity.sh` runs Python and Rust `--both` against `fixtures/e2e_site` on a local http.server: embedded JSON, `_original.json`, ZIP contents byte-identical; metadata/failed_images/failed_assets identical except timestamps. Naming fixture `fixtures/output/names.json` (46 URLs, 22 components) all match.
  - Not yet ported: Python's `cyoa_downloader.log` file in output dir, `backup_report.txt` append path, audio copy to output, offline viewer, cyoa-manager registration, `-L` list mode, ICC/website/cyoap-vue modes, proxy/DNS/VPN/Cloudflare flags.
- **Totals:** 141 tests pass; fmt + clippy clean.
- **Sol review of output stage → 9 findings, 8 fixed by Claude** (commit caa9990; see SECURITY_REVIEW). Luna could not write any file (create or modify) from this point on.
- **GUI Batches A–C (`cyoa-desktop`, egui/eframe 0.37 + glow, Claude-implemented):**
  - `worker.rs`: dedicated thread with a 3-thread Tokio runtime and bounded command and event channels. Progress events are lossy (`try_send`); Started/Finished are always delivered. Queue runs sequentially with a per-job CancellationToken; shutdown is bounded and drains events to avoid deadlock.
  - `app.rs`: URL validation, output folder with Browse (rfd), Embedded/ZIP/Both, threads/wait (Python defaults 4/60), virtualized queue and log (`show_rows`), 5000-line ring log, at most 2000 events drained per frame, persisted settings, worker waker → `request_repaint`.
  - Measured (release, this machine): window opens; **0 ms CPU over 5 s idle**; 140.6 MB working set; exe 17.0 MB.
  - Not yet measured: download through the GUI (needs manual or UI-automation run), 100k-row queue frame time, IME and high-DPI (prototype gate).
- **Totals:** 152 tests pass; fmt + clippy clean.
- **Benchmarks** (BENCHMARKS.md). They exposed 2 parity bugs, both fixed:
  1. Rust lacked Python's 0.3 s per-domain request interval.
  2. Rust never used the persistent image cache (Python always does: `~/.cyoa_downloader/image_cache`, 2048 MB).
  - Cache is now wired via `RunOptions.cache_dir/cache_limit_mb`. Tests and `e2e_parity.sh` use an isolated `USERPROFILE`/`HOME`.
  - Rust↔Python cache interop verified (300/300 hits).
- **Batch import / `-L` (Claude-implemented):**
  - `cyoa-engine::batch` ports `import_queue_items_from_file` for .txt and .csv. CSV follows pandas `read_csv` semantics, because pandas 3.0.2 is installed and used. Fixture `fixtures/batch` (11 files, via `export_batch.py` against the real importer) all match.
  - CLI now has a positional `filename`, `-L/--list` and `failed_urls.txt` (append format). Exit codes: 2 for bad input, 1 when any item failed, 130 on cancel.
  - `RunOptions.file_name` output-name override.
  - Discovery failures now report Python's exact "Could not resolve project data …" message.
  - Live `-L` parity against Python: renamed ZIP identical; failed_urls.txt identical except timestamp; both exit 1.
  - Unsupported batch modes (website/ICC/pure/cyoap-vue) fail per item with an explicit message; they are never silently downgraded.
  - DEFERRED: .xlsx/.xls, remote/Google Sheets lists.
  - Also fixed a flaky CLI test: Windows accepted sockets inherit non-blocking mode.

## Update — session 5 (2026-11-18)

**KI-R1 fixed (Claude):** payload spooling plus streamed embed output.
- `fetch::Payload` (Mem/File); `FetchCtx.spool_dir`; `ProcessOptions.embed_out`; `ProcessResult.embed_file/embed_changed`.
- The run temp dir is split into `pkg/` (ZIP), `spool/` and `embedded.json`.
- Dedup hashing and asset writes are streamed from the spool files.
- New test: spooled output is byte-identical to in-memory output. E2E parity 6/6. 300-image JSON is identical to Python.
- Peak memory: 98 MB → 28 MB.

**GUI batch D (Claude):**
- "Import list…": .txt/.csv through `cyoa_engine::batch`, with the dialog and parsing on their own thread.
- Per-item filename and mode. Empty or "auto" uses the GUI default; unsupported modes fail visibly.
- "Clear finished" bounds the queue history.
- New tests:
  - The GUI worker downloads `fixtures/e2e_site` end to end (progress reaches the UI channel, the waker fires, JSON and ZIP are written, the 404 is counted).
  - 100k-row frame time: 0.09 ms release.

**Website/ICC mode started (plan: docs/WEBSITE_MODE_PLAN.md, audit by Luna via luna_review):**
- Batch W1 `cyoa-engine::website` (Claude) ports `_normalize_remote_url`, `_normalize_cache_key`, `_safe_filename`, `_kind_from`, `_reserve_local_path`/`_allocate_local_path` (sequential collisions, case-insensitive) and `_local_asset_reference`.
- Fixture `fixtures/website/paths.json` comes from the real `WebsiteDownloader` (`export_website_paths.py`). All 74 cases match.
- Known approximation: identity uses `to_lowercase`, where Python uses `casefold`.
- Next: W2, the HTML/CSS/JS reference rewrite with golden fixtures.
- Totals: 165 tests pass.
- **W2-CSS** (Claude): `website::Rewriter::process_css`, the `AssetSink` trait, `_rewrite_direct_urls` (CSS context, with a look-around-free scanner for `_quoted_asset_re`), `_existing_local_asset` and `_asset_kind_from_path`.
  - Fixture `fixtures/website/css.json` (12 cases from the real `_process_css` with a stubbed `_download_asset`).
  - Output AND download-call sequence match Python.
- **W2-JS** (Claude): `website::js` ports the following:
  - `_process_js` (dynamic-loader guard, app-bundle guard, vercel stub)
  - `_download_runtime_template_assets` (direct arrays with priority ordering, module-relative paths, indexed preload prefixes, chunk maps, numeric random ranges, `${obj.prop}` templates)
  - `_rewrite_known_downloaded_urls`, JS-context `_rewrite_direct_urls`, `_infer_runtime_chunk_paths`, `asset_scan._infer_dynamic_asset_paths`
  - Uses `fancy-regex` 0.11 (already in the local cache; added offline) for back-reference patterns.
  - Fixture `website/js.json`: 14 `_process_js` cases, with output and download-call sequence, plus chunk and dynamic-path cases. All match. A mutation check confirmed the test detects differences.
  - Python runs image prefetches in parallel when `max_workers>1`, so its order is nondeterministic there. Rust is sequential, which equals Python with one worker.
  - 169 tests pass.
- **Concurrent-session incident:** a second Claude Code session edited `cyoa-storage/cache.rs`, `atomic_io.rs` and `cyoa-engine/fetch.rs` (streamed spool + cache-from-file). The owner cancelled it.
  - Its changes compiled and passed 172 tests, but are unreviewed. They are saved as `git stash@{0}` ("other-session: …") and NOT applied.
  - Review with `git stash show -p stash@{0}`; apply with `git stash apply stash@{0}`.
- **W2-HTML step 1** (Claude): `website::html` is a byte-compatible port of `str(BeautifulSoup(x, "html.parser"))` (bs4 4.14.3, CPython 3.12.10 `html.parser`). It covers:
  - the two-phase feed/close tokenizer with its quirks
  - the bs4 tree builder (void elements, `already_closed_empty_element`, `_popToTag`, whitespace collapsing)
  - minimal formatter, sorted and multi-valued attributes, the meta charset→utf-8 rewrite
  - `html.unescape` and the bs4 entity/charref tables (dumped from Python as data)
- **Verification:** 48 hand-picked cases plus 2000 seeded differential-fuzz cases (`export_html_fuzz.py`) are all byte-identical. A mutation check passed. 172 tests total.
- **W2-HTML step 2** (Claude): `website::page` ports `_download_html`/`_localize_html`, `_set_attr_local` (srcset, same-origin basename fallback, integrity/crossorigin removal), telemetry/Cloudflare script removal, `<base>`, custom-viewer route, link rel/as rules, lazy attributes, iframe same-origin, svg use/image, `<style>`/`style=` CSS, the dice fallback, `_decode_inline_document_payload`, `_same_origin`, `_is_archive_noise_url` and the audio patch text.
  - The injected JS blobs are taken verbatim from the Python source via `ast` (fixture `embedded`).
  - Fixture `website/html_localize.json`: 12 cases. Output HTML, download-call sequence and route flag all match. A mutation check passed.
  - W2 is complete. Next: W3, the classic static mirror (real fetch through cyoa-net, `_download_asset_impl` with recursive CSS/JS/HTML, containment-checked writes).
  - 175 tests pass.
- **W3a classic mirror** (Luna drafted `mirror.rs` via `luna_review` because `luna_implement` failed twice to create files under `src/website/`; Claude fixed and integrated it):
  - `website::mirror::MirrorSink` implements AssetSink over cyoa-net, running blocking on a Tokio handle. It ports `_download_asset_impl` (telemetry skip, SSRF block, in-progress guard, cached failures, root and /assets/ fallbacks, content-type mismatch, recursive css/js/html, depth 16) and `_fetch`.
  - `examples/mirror_classic.rs`.
  - **Proof:** `bash scripts/website_parity.sh` runs Python `_download_html` against Rust `mirror_classic` on `fixtures/site_mirror` (local http.server). The file list, all 14 files and the failed list are byte-identical.
  - DEFERRED: checkpoint/304, browser transport, VPN guard, parallel prefetch, deep scan, `repair_missing_entry_scripts`, ICC project integration, `_site.zip`.
  - 176 tests pass.
