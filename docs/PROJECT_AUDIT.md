# Python project audit (Phase 1)

> Historical audit of the Python baseline (2026-11-18). Rust status: `docs/MIGRATION_PROGRESS.md`; security follow-up: `docs/SECURITY_REVIEW.md`.

Status: read-only audit, 2026-11-18. Sources: Claude inspection, Luna repository audit, Sol security and architecture review. Claims that Claude verified are marked ✔. File references are relative to `cyoa_downloader_app/` unless noted.

## 1. Size and shape

- Version 1.1.5. About 60k lines of application Python across roughly 140 modules, plus 46 test files (`tests/test_program.py` alone is about 14.5k lines) and about 55 tool, benchmark and smoke scripts.
- The largest modules are `gui/app.py` (over 12k lines ✔), `gui/final_behaviors.py` (3.7k), `download/website.py` (3.1k), `download/image_pipeline.py` (1.7k), `integrations/offline_viewers/modernizer.py` (1.6k), `runtime/surface.py` (1.4k) and `download/asset_scan.py` (1.3k).
- Entry point: `cyoa_downloader.py` → `cli.main()`. With no arguments, or with `--gui`, it starts the Tk GUI; any other arguments run the argparse CLI ✔.
- There is a large compatibility layer: `compat.py`, `runtime/surface.py` and the `_bridge.py` modules mirror a legacy global symbol table. **This is not ported.** Rust gets explicit module APIs instead.

## 2. Subsystems

| Area | Modules | Notes |
|---|---|---|
| CLI | `cli.py` | About 90 flags: output modes, archive strategy, network/DNS/proxy/VPN/Cloudflare, integrations, maintenance utilities |
| GUI | `gui/*` (customtkinter) | Queue, log, progress, settings, library/history/results panels, cache, diagnostics, CYOA Manager, viewers, AI, guide, credits, updates |
| Project discovery and parsing | `project/discover.py`, `parse.py`, `cyoa_cafe.py`, `cyoap_vue.py`, `mediawiki.py` | ICC `project.json` discovery, JSON5 fallback, CYOA.CAFE static/API paths, CYOAP Vue |
| Download engine | `download/orchestrator.py`, `image_pipeline.py`, `website.py`, `asset_scan.py`, `route_crawler.py`, `package.py`, `fonts.py`, `audio_download.py` | Embedding, ZIP packaging, website mirroring, story-route crawl, font localization, audio |
| Network | `network/*` | requests sessions, 20 global / 12 per-host admission (`budget.py:14-15` ✔), retry policy, throttling, proxy, custom DNS, VPN guard, Cloudflare and FlareSolverr, Playwright browser owner, runtime capture |
| Storage | `storage/*`, `core/atomic_io.py` | Settings JSON, image cache index, history JSON, resume state, SQLite archive checkpoints |
| Integrations | `integrations/*` | Offline viewer injector and modernizer, CYOA Manager (SQLite), gallery-dl, itch-dl, Discord attachment refresh, AI providers, plugins |
| Diagnostics | `diagnostics/*` | Dependency check, self-test, update check, startup probe, reports |

## 3. Concurrency model (verified in part)

- Threads: 36 `threading.Thread(` sites and 31 `ThreadPoolExecutor` sites. The GUI clamps workers to 32; font pools are capped at 8 and 6.
- HTTP admission budget: 20 global and 12 per host, with priority and FIFO waiters, cancellation checks and leases ✔.
- Playwright runs on a single-worker owner executor (`network/browser_owner.py`) ✔.
- The GUI drains progress, log and import queues through 65 `.after()` callbacks, with per-tick budgets.
- Shared mutable state includes HTTP sessions, the cache index, compatibility globals and GUI telemetry. Some of it is protected by locks.
- No multiprocessing; asyncio appears in only 5 references.

## 4. GUI responsiveness

**This affects the main motivation for the migration.** The Python GUI already has extensive responsiveness work (see `docs/PERFORMANCE.md` and `docs/OPTIMIZATION_ROADMAP.md`): a virtual queue, paged cards, off-thread queue import, panel jobs, background history metadata, and bounded log and progress turns.

Confirmed blocking on the Tk thread:
- Window icon and logo decoding with Pillow at startup (low impact).

Suspected, not yet measured:
- Synchronous history and library loading when a panel opens.
- Tk widget and font construction cost at startup.
- Queue copy and membership scans with large queues (listed as remaining in the roadmap).

**Not found:** blocking HTTP calls (`requests.get/post`, `urlopen`) inside `gui/*.py` ✔. The only `time.sleep` is a 1 ms yield in `run_preparation.py:31` ✔.

**Implication:** freezing in the Python app most likely comes from Tk's single-threaded rendering cost, GIL contention from worker threads, and widget construction, not from network calls on the UI thread. A Rust GUI can address the first two, but **improvement has to be measured against the Python benchmarks in `tools/benchmark_gui_latency.py` and `tools/benchmark_idle_gui.py`**, not assumed.

## 5. Persistent data (compatibility contract)

| Data | Location | Format |
|---|---|---|
| Settings | `~/.cyoa_downloader/settings.json` ✔ | Flat JSON. Unreadable files are backed up to `.corrupt`. Portable export removes secrets |
| Secrets | OS keyring, service "CYOA Downloader", or plain settings | The legacy keyring token is moved into plain settings (`config/settings.py:371-386` ✔) |
| Image cache | `~/.cyoa_downloader/image_cache/index.json` ✔ | JSON index plus payload files |
| History | `~/.cyoa_downloader/download_history.json` ✔ | JSON |
| Resume | `download_state.json` in the output folder ✔ | JSON |
| Archive checkpoints | `~/.cyoa_downloader/archive-checkpoints/` ✔ | SQLite tables `owner`, `records(kind,url,data)` and `archive` (`storage/archive_checkpoint.py:165-167` ✔) |
| CYOA Manager | External library DB | SQLite tables `library_meta` and `library_projects` (`integrations/cyoa_manager.py:139-143` ✔) |
| Reports | Output folder | `backup_report.txt`, `failed_assets.txt`, `failed_images.txt`, `skipped_youtube_audio.txt`, `conversion_report.json`, `archive_manifest.json`, checksum manifest |

The Rust port must read and write these formats compatibly unless the owner approves a change.

## 6. Tests

- 1633 pass and 21 are skipped on Windows (see `ENVIRONMENT.md`). The tests are largely behavioral and use local fixtures and mocked HTTP. Many inspect Python internals and source text (legacy symbol markers), so they **cannot be reused** for Rust.
- Reusable as baseline fixtures: the `examples/` batch files, archive/ZIP fixtures, URL and path sanitization cases, JSON summary cases, and the output folder layouts produced by local-server tests. Phase 3 will export these into `fixtures/` as language-neutral golden files (input → expected output).

## 7. Audit agent quality notes

- Luna: accurate citations; one truncated output. Sol: accurate, verified. MiMo and Qwen: unreliable on this codebase (see `ENVIRONMENT.md`). Use them only for narrow tasks with checkable outputs.
