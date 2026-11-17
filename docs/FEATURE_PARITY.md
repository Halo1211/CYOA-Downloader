# Feature parity matrix

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
| F-016 | Proxy (manual/env/per-scheme/bypass) | `network/proxy.py` | cyoa-net | 3 | L | |
| F-017 | Custom DNS (UDP/TCP/DoH/DoT, IPv6, fallback) | `network/dns.py` | cyoa-net | 3 | L+S | |
| F-018 | VPN interface guard | `network/vpn.py` | cyoa-net | 3 | L | Windows interface enumeration |
| F-019 | Cloudflare detection and FlareSolverr | `network/cloudflare.py` | cyoa-net | 6 | L | cloudscraper itself is deferred (D3) |
| F-020 | Project discovery (ICC project.json, JSON5) | `project/discover.py`, `parse.py` | cyoa-parse | 4 | L+M | Regex rewrites |
| F-021 | CYOA.CAFE support | `project/cyoa_cafe.py`, `download/cyoa_cafe_static.py` | cyoa-parse | 4 | L | |
| F-022 | CYOAP Vue folder/zip/website | `project/cyoap_vue.py` | cyoa-engine | 4 | L | |
| F-023 | MediaWiki source | `project/mediawiki.py` | cyoa-parse | 4 | M | |
| F-024 | Embedded JSON output (data URIs) | `download/orchestrator.py`, `image_pipeline.py` | cyoa-engine | 4 | L | |
| F-025 | ZIP / Both outputs | `download/package.py` | cyoa-engine | 4 | L | |
| F-026 | ICC folder/zip website archive | `download/website.py` | cyoa-engine | 5 | L+S | |
| F-027 | Pure website folder/zip, Classic and Smart strategies, route crawl | `download/website.py`, `route_crawler.py`, `archive_*` | cyoa-engine | 5 | L+S | HTML parser differences |
| F-028 | Browser strategy, runtime capture, Auto | `network/browser_owner.py`, `runtime_capture.py`, `archive_profiler.py` | cyoa-integrations | 6 | S | CDP; SEC-1 |
| F-029 | Deep asset scan | `download/asset_scan.py` | cyoa-parse | 5 | L | |
| F-030 | Font localization and analysis | `download/fonts.py` | cyoa-engine | 5 | L | |
| F-031 | Audio recovery and reports (yt-dlp) | `download/audio_download.py`, `audio_reports.py` | cyoa-integrations | 6 | L/X | |
| F-032 | Image conversion and processing | `download/image_pipeline.py` | cyoa-engine | 4 | L | |
| F-033 | Retry assets/images/audio | `download/website_recovery.py` | cyoa-engine | 5 | L | |
| F-034 | Verify package and write manifest | `download/package.py` | cyoa-engine | 4 | L | |
| F-035 | Reports (backup_report, failed_*.txt) | various | cyoa-engine | 4 | L | Byte-compatible text |
| F-036 | Batch import (TXT/CSV/XLSX/XLS/remote CSV) and export | `importers/batch.py`, `gui/queue_import.py` | cyoa-engine | 4 | L+M | calamine |
| F-037 | Offline viewer registry, injection and modernization | `integrations/offline_viewers/*` | cyoa-integrations | 7 | L+S | 3.7k lines; large |
| F-038 | CYOA Manager library (SQLite) | `integrations/cyoa_manager.py` | cyoa-integrations | 7 | L | |
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
