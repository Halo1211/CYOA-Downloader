# Feature parity matrix

## Status update (2026-11-18 ~05:45) — this section overrides the row statuses below

| ID | Status | Evidence |
|---|---|---|
| F-001 settings | TESTED | 30 Python golden cases, byte-identical `settings.json` formatting |
| F-002 secrets | TESTED (intentional change DL-5) | MemoryBackend tests; the native keyring is not yet checked against Python's real entries |
| F-003 paths | TESTED + REVIEWED (Sol) | 110 golden cases plus junction/dangling-link tests |
| F-004 url utils | TESTED + REVIEWED (Sol) | 111 golden cases |
| F-005 atomic I/O | TESTED | Byte parity for JSON writes |
| F-006 ZIP validation | TESTED | 9 Python verdicts, including the symlink attribute |
| F-008 image cache | TESTED | Unit tests plus the Python index format; Sol review pending |
| F-009 history | PARTIAL | Record path byte-identical; background metadata probe pending |
| F-010 resume | TESTED | Bytes and job keys identical to Python |
| F-011 checkpoints | TESTED | DB names (`path_for`) and payloads identical to Python; interoperable |
| F-012 HTTP client | IMPLEMENTED + TESTED (wiremock) | Policy resolver, redirects, caps, cancel; page HTML decoded with `try_decode_bytes` like Python `get_source` (s6, BOM/UTF-16 test; invalid UTF-8 → U+FFFD in both, verified against Python) |
| F-013 admission | TESTED | 7 async tests (Sol design) |
| F-014 retry | TESTED | Python-derived schedules (Sol) |
| F-015 throttle | TESTED | Python window accounting |
| F-020b static project discovery (network) | IMPLEMENTED + TESTED (wiremock) | `discover_net::get_project_source`; catalog/browser/AI/archive paths DEFERRED |
| F-024 embedded JSON output | TESTED (E2E byte parity) | `rust/scripts/e2e_parity.sh`: embedded JSON and `_original.json` byte-identical; 300-image benchmark byte-identical |
| F-025 ZIP / Both outputs | TESTED (E2E parity) | ZIP entries byte-identical; no-clobber publishing; entry-name validation (Sol-reviewed) |
| F-025b reports (`_metadata.json`, `failed_images.txt`, `failed_assets.txt`, `backup_report.txt` append, `cyoa_downloader.log`) | TESTED (E2E byte-exact, timestamps excluded; log redaction vs Python fixture) | s6: backup_report append + stale HTML cleanup, CRLF text mode on Windows, rotating log file |
| F-012b media fetch loop (`fetch_one`) | IMPLEMENTED + TESTED | cache, 429/5xx backoff, 404/410, 401/403, error-doc rejection, 3 transport retries; gallery-dl/headless/cookies/Discord/MediaWiki/VPN DEFERRED |
| F-015b per-domain 0.3 s interval | TESTED | Matches Python `_domain_min_interval` (benchmark: 300 req ≈ 90 s in both) |
| F-008b image cache in pipeline | TESTED (interop) | Python reads a Rust-written cache: 300/300 hits |
| CLI MVP (url,-u,-o,-z,-b,-t,-w) | TESTED | `-L` list mode, website/ICC/cyoap-vue modes, proxy/DNS/VPN/Cloudflare flags NOT ported |
| GUI (egui) A–C | IMPLEMENTED + TESTED (worker unit tests, launch smoke) | Download via GUI not yet exercised manually; Python GUI features beyond the single-URL queue NOT ported |
| Website W1 path mapping | TESTED | 74 Python fixture cases match: URL normalization, kind detection, local path allocation, local references (casefold identity, `index.html` reserved) |
| Website W2-CSS rewrite | TESTED | 12 cases: output and download-call sequence match Python (@import, url(), quoted/embedded URLs) |
| Website W2-JS rewrite | TESTED | 14 `_process_js` cases plus chunk-map/dynamic-path cases match; Rust prefetch is sequential (= Python with 1 worker; Python parallel order is nondeterministic) |
| Website W2-HTML (BeautifulSoup html.parser) | TESTED | 48 cases + 2000 seeded fuzz cases byte-identical; `_localize_html` 12 cases identical. Parity exception: nesting >512 rejected |
| Website W3a classic mirror | PARTIAL | `rust/scripts/website_parity.sh`: file list, 14 files, failed list byte-identical vs Python `_download_html`. Not ported: checkpoint/304, browser transport, VPN guard, deep scan, `repair_missing_entry_scripts`, ICC project integration, `_site.zip`, CLI `--icc` |
| F-020 project parse | PARTIAL | `parse.py` text functions: 18 inputs × 5 functions identical; archive-bytes extraction pending |
| Policy (SEC-1/SEC-6) | TESTED | 43 host classes plus 8 SSRF decisions identical to CPython 3.12 |
| Headers | TESTED | 15 URLs; headers and key order identical |

## Intentional behavior differences (Rust is equal or better; each is covered by a test)

1. **DL-5 secrets:** stored in the keyring by default; legacy plaintext is migrated into the keyring and never written back out.
2. **DL-8 paths:** any link or junction component inside the output root is rejected. This is stricter than Python.
3. **Checkpoint `get`:** always re-hashes, because NTFS ChangeTime cannot be read without `unsafe`.
4. **Parse, out-of-range floats (`1e400`):** Python emits the invalid JSON token `Infinity`; Rust emits `null`, which is valid JSON.
5. **Parse, non-UTF-8 decoding:** Python's chardet misdetected latin-1 and Shift-JIS samples (`"ZÈZ"`, `"𣲚塲"`); Rust's fixed fallback chain decodes them correctly (`"été"`, `"日本"`).
6. **HTTP:** connections go only to DNS answers vetted by the policy (closes the Python rebinding window, SEC-6). Downloads have a byte cap (SEC-4).
7. **Copy/report APIs:** return structured reports instead of logging and skipping.

Known parity gaps:
- ZIP duplicate detection uses `to_lowercase`, where Python uses `casefold` (ß).
- Some settings edge numerics: huge ints, NaN, unpaired surrogates.
- History has no inter-process file lock.

Statuses: NOT_STARTED · IN_PROGRESS · IMPLEMENTED · TESTED · REVIEWED · VERIFIED · BLOCKED.

Every feature below is **NOT_STARTED**. This is the Phase 1 inventory; Phase 3 adds detailed inputs and outputs, fixtures and tests to each row.

Agent: L = Luna, S = Sol review, C = Claude, M = MiMo proposal (verified), X = external executable.

Source paths are relative to `cyoa_downloader_app/`.

| ID | Feature | Python source | Rust destination | Stage | Agent | Notes / limits |
|---|---|---|---|---|---|---|
| F-001 | Settings load/save, defaults, `.corrupt` backup, portable export/import | `config/settings.py` | cyoa-storage | 1 | L+M | Must stay compatible with `settings.json` keys |
| F-002 | Secrets (keyring/plain) | `config/secrets.py` | cyoa-storage | 1 | L+S | Owner decision D5 |
| F-003 | Path sanitization and safe join (reserved names, links, truncate+hash) | `core/paths.py` | cyoa-core | 1 | L+S | Python `strip` semantics |
| F-004 | URL canonicalization and internal-host policy | `core/url_utils.py` | cyoa-core | 1 | L+S | SEC-1, SEC-6 |
| F-005 | Atomic file writes | `core/atomic_io.py` | cyoa-storage | 1 | L | |
| F-006 | ZIP validation and creation | `core/archive.py` | cyoa-storage | 1 | L+S | SEC-5, fuzzing |
| F-007 | JSON summary and embed streaming | `core/json_summary.py`, `embed_io.py` | cyoa-core | 1 | L | |
| F-008 | Image cache (index.json) | `storage/cache.py` | cyoa-storage | 2 | L | |
| F-009 | Download history | `storage/history.py`, `history_metadata.py` | cyoa-storage | 2 | L | |
| F-010 | Resume state (`download_state.json`) | `storage/resume.py` | cyoa-storage | 2 | L | |
| F-011 | SQLite archive checkpoints | `storage/archive_checkpoint.py` | cyoa-storage | 2 | L+S | Same schema |
| F-012 | HTTP client, headers, sessions, cookies | `network/fetch*.py`, `headers.py`, `sessions.py` | cyoa-net | 3 | L+S | |
| F-013 | Admission budget 20 global / 12 per host | `network/budget.py` | cyoa-net | 3 | S | |
| F-014 | Retry policy and status retries | `network/retry_policy.py` | cyoa-net | 3 | L | |
| F-015 | Bandwidth throttle | `network/throttle.py` | cyoa-net | 3 | L | |
| F-016 | Proxy (manual/env/per-scheme/bypass) | `network/proxy.py` | cyoa-net | 3 | L | PARTIAL: `cyoa_net::proxy` (modes, validation, redaction, manual per-scheme map, bypass, reqwest wiring); redaction/validation checked against Python. CLI flags + saved-settings resolution done (Python cli.py 580-599). GUI panel done (s6). CLI flags persisted to settings.json after validation like Python cli.py 685-723 (s6, test `network_flags_are_persisted_to_settings_like_python`; proxy URLs are plain JSON values like Python; only API keys/tokens use the keyring). GUI network panel reads/writes the same Python keys in settings.json (s6, `NetSettings::load_shared/save_shared`, defaults equal Python `_SETTINGS_DEFAULTS`). Not yet: browser/FlareSolverr proxy config. Diff: inherit_env uses reqwest env discovery, not requests.get_environ_proxies |
| F-017 | Custom DNS (UDP/TCP/DoH/DoT, IPv6, fallback) | `network/dns.py` | cyoa-net | 3 | L+S | PARTIAL (DNS-1): pure helpers in `cyoa_net::dns` (query wire, response parse, endpoint split, protocol inference, validation) byte/text-identical to Python on `rust/fixtures/dns_pure.json` (`tests/dns_parity.rs`). Python quirk kept: owner-name pointers skipped not followed. DNS-2/3: UDP/TCP/DoT/DoH transports, 300 s cache, IPv6 toggle, system fallback, wired into `PolicyResolver` (SSRF check applies to custom answers, test `custom_dns_internal_answer_is_rejected`), CLI --dns/--dns-protocol/--dns-port/--dns-timeout/--[no-]dns-fallback-system/--[no-]dns-ipv6/--bebasdns with saved-settings fallback. Sol-reviewed (SECURITY_REVIEW s6). Diffs: DoH https-only/no redirects, replies must echo the question, UTS-46 IDNA. GUI panel done (s6). CLI DNS/VPN flags persisted to settings.json (s6). Not yet: DoT network test |
| F-018 | VPN interface guard | `network/vpn.py` | cyoa-net | 3 | L | PARTIAL: `cyoa_net::vpn` (name matching verified identical to Python on 17 names, policy parsing, 15 s cache, fail-closed), enforced in `FetchClient::get` before any I/O (test: no request reaches server), asset failure text = Python, CLI --vpn-policy/--vpn-interface + saved settings. Diff: PowerShell outputs tab lines instead of JSON (same .NET query). UNTESTED: real Windows enumeration (live PowerShell run not permitted in session 6). GUI policy/interface settings done (s6). Not yet: GUI live VPN status display, browser paths |
| F-019 | Cloudflare detection and FlareSolverr | `network/cloudflare.py` | cyoa-net | 6 | L | cloudscraper itself is deferred (D3) |
| F-020 | Project discovery (ICC project.json, JSON5) | `project/discover.py`, `parse.py` | cyoa-parse | 4 | L+M | Regex rewrites |
| F-021 | CYOA.CAFE support | `project/cyoa_cafe.py`, `download/cyoa_cafe_static.py` | cyoa-engine | 4 | L | PARTIAL (s6): `cafe::CafeResolver` (Luna; PocketBase record/slug/iframe lookups, candidate validation, TTL caches) identical to Python on 10 recorded transcripts (`fixtures/cafe.json`: same request sequence incl. duplicates, same result/error); wired into discovery (3-hop catalog resolution + React-shell `/game/` redirect) over FetchClient (adapter unit-tested). Static `cyoa_pages` records exported as an offline gallery in ICC/Pure modes (`cafe_static.rs`, Luna; byte-identical to the Python fixture incl. manifest/metadata; Python `failures` order is completion order, Rust uses entry order). Not yet: live check against cyoa.cafe |
| F-022 | CYOAP Vue folder/zip/website | `project/cyoap_vue.py` | cyoa-engine | 4 | L | |
| F-023 | MediaWiki source | `project/mediawiki.py` | cyoa-parse | 4 | M | |
| F-024 | Embedded JSON output (data URIs) | `download/orchestrator.py`, `image_pipeline.py` | cyoa-engine | 4 | L | |
| F-025 | ZIP / Both outputs | `download/package.py` | cyoa-engine | 4 | L | |
| F-026 | ICC folder/zip website archive | `download/website.py`, orchestrator.py 636-785 | cyoa-engine | 5 | L+S | DONE for archive strategies auto (default), classic and smart (route crawl, s6): CLI `--icc` / `--icc-folder` / `--no-deep-scan`, batch modes website_zip / website_folder. `rust/scripts/icc_parity.py` runs the Python and Rust CLIs on a local ICC site: identical output for --icc-folder [auto], --icc [auto] (ZIP entries) --icc-folder [classic] (pre- and post-project deep scan) and --icc-folder [smart] (route crawl: routes/, redirects, collisions, non-HTML failures, link rewriting, smart manifest). ArchivePolicy read from settings `archive_*` with Python bounds. Diffs: `_site.zip` no-clobber; deep-scan failure text keeps HTTP status; deep-scan registration order sorted (Python order follows randomised set hashing); CYOA.CAFE viewer-entry rewrite, browser strategy (CDP runtime capture) and the headless route retry, `--fonts`, modernization (F-037) not ported (browser is refused with a clear error) |
| F-027 | Pure website folder/zip, Classic and Smart strategies, route crawl | `download/website.py`, `route_crawler.py`, `archive_*` | cyoa-engine | 5 | L+S | DONE except Browser (s6): CLI `--pure-website` / `--pure-website-folder`, batch pure_website*, GUI modes; Auto profile (`profile_archive_target`, Luna, fixture-verified), route crawl (`routes.rs`, Luna + Claude parity fixes), captured-project recovery. `rust/scripts/icc_parity.py` pure runs identical to Python: auto (ch1 entry -> Classic profile), classic, smart. Diffs: when Auto picks Browser Rust runs the Smart crawl (no CDP capture, logged); CYOA.CAFE static records, Perchance and `--fonts` not ported; deep-scan failure/registration order normalised (Python set order is randomised) |
| F-028 | Browser strategy, runtime capture, Auto | `network/browser_owner.py`, `runtime_capture.py`, `archive_profiler.py` | cyoa-integrations | 6 | S | CDP; SEC-1 |
| F-029 | Deep asset scan | `download/asset_scan.py` | cyoa-engine | 5 | L | PARTIAL (s6): `scan_file_for_assets` identical to Python on 1889 differential cases (`fixtures/scan_file.json`), incl. 2 MiB structural JSON path; deep-scan path helpers identical on 299 cases (`fixtures/deep_paths.json`). Diff: back-reference / look-behind patterns rewritten as linear regexes (same matches; fancy-regex stalled on multi-MB input). Generated Entry images (Luna, verified on Python fixture). BFS driver `website::deep_driver::DeepScan` verified end-to-end by `rust/scripts/deep_scan_parity.py` (local server, every fallback: CSS page-root, viewer route, round 2 rescans, HTML-error rejection): identical file tree and URL->path map. Diffs: failure text keeps the real status (`HTTP 404`) where Python says `HTTP request failed`; candidates processed in sorted order; no HTTP/2 client. Not ported: scanner plugins (D4), AI round |
| F-030 | Font localization and analysis | `download/fonts.py` | cyoa-engine | 5 | L | |
| F-031 | Audio recovery and reports (yt-dlp) | `download/audio_download.py`, `audio_reports.py` | cyoa-integrations | 6 | L/X | |
| F-032 | Image conversion and processing | `download/image_pipeline.py` | cyoa-engine | 4 | L | |
| F-033 | Retry assets/images/audio | `download/website_recovery.py` | cyoa-engine | 5 | L | |
| F-034 | Verify package and write manifest | `download/package.py` | cyoa-engine | 4 | L | |
| F-035 | Reports (backup_report, failed_*.txt) | various | cyoa-engine | 4 | L | Byte-compatible text |
| F-036 | Batch import (TXT/CSV/XLSX/XLS/remote CSV) and export | `importers/batch.py`, `gui/queue_import.py` | cyoa-engine | 4 | L+M | calamine |
| F-037 | Offline viewer registry, injection and modernization | `integrations/offline_viewers/*` | cyoa-integrations | 7 | L+S | 3.7k lines; large |
| F-038 | CYOA Manager library (SQLite) | `integrations/cyoa_manager.py` | cyoa-integrations | 7 | L | PARTIAL (s6): `cyoa_integrations::cyoa_manager` add / add_archive / list / find / scan / viewer_pref + CLI `--cyoa-manager` (saved `cyoa_mgr_db_path`, else auto-detect). Interop verified on a Python-written DB: Rust sees Python entries as duplicates and writes identical rows (id/date excluded; ignored test `python_interop`). Diffs: project check streams (shape only, memory bounded) instead of `load_json_summary`; Python accepts NaN/Infinity, Rust rejects; empty LOCALAPPDATA candidates skipped (Python checks a cwd-relative path); ZIP members with ':' rejected; registers the ZIP actually written (no-clobber name) where Python assumes `{name}.zip`. GUI (s6): "Add finished downloads to CYOA Manager" toggle + DB path, stored in settings.json as `cyoa_mgr_enabled`/`cyoa_mgr_db_path`; shared `register_output` used by CLI and GUI; GUI logs when no library is found (Python is silent). Not yet: `prepare_cyoa_manager_serve_folder` (needs F-037), the GUI library browser/import panel. GUI toggle not exercised manually |
| F-039 | Local preview server (Serve) | `gui/app.py:11936+`, `preview_server.py` | cyoa-integrations | 7 | L+S | Fix SEC-2 |
| F-040 | gallery-dl fallback | `integrations/gallery_dl.py` | cyoa-integrations | 6 | X | |
| F-041 | itch.io (itch-dl) | `integrations/itch*.py` | cyoa-integrations | 6 | X | |
| F-042 | Discord attachment refresh | `integrations/discord_attachments.py` | cyoa-integrations | 6 | L | Token in the keyring |
| F-043 | AI integration (providers, budgets) | `integrations/ai*.py` | cyoa-integrations | 8 | L | Owner decision D4 |
| F-044 | Plugins | `integrations/plugins.py` | — | 8 | C | Python plugins cannot run in Rust. D4 |
| F-045 | Browser cookie import | `network/browser.py` | — | — | — | Defer (D3) |
| F-046 | Selenium fallback | various | — | — | — | Drop (D3) |
| F-047 | CLI (about 90 flags) | `cli.py` | cyoa-cli | 4→8 | L | Flag names kept |
| F-048 | GUI: queue (virtual), log, progress, run controls | `gui/*` | cyoa-desktop | 9 | L+S | |
| F-049 | GUI: settings panels (network, viewers, archive, AI…) | `gui/final_behaviors.py`, `panels/*` | cyoa-desktop | 9 | L | |
| F-050 | GUI: library, history, results, cache, diagnostics panels | `gui/*_panel.py`, `cache_maintenance.py` | cyoa-desktop | 9 | L | |
| F-051 | Diagnostics: dependency check, self-test, startup report | `diagnostics/*` | cyoa-cli | 8 | L | |
| F-052 | Update check | `diagnostics/updates.py` | cyoa-cli | 8 | L | |
| F-053 | Localization (`--language`) | `cli.py`, gui | cyoa-desktop | 9 | L | Scope to be confirmed |
| F-054 | Logging (file plus GUI) | `logging_setup.py`, `gui/logging_ui.py` | all | 1 | L | `tracing` |
| F-055 | Windows packaging | `CYOA-Downloader.spec`, `tools/build_windows.ps1` | — | 10 | C | |

## Untested behaviors (to record)

- 21 Python tests are skipped (live, GUI and Playwright). The browser, GUI-smoke and live-network paths have no automated baseline.
