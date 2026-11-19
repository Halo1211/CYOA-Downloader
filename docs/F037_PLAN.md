# F-037 Offline viewers: audit and migration plan (2026-11-19, V1–V3 approved and done 2026-11-19; V4–V5 await approval)

## Audit (Python, 4,086 lines)
| File | Lines | Contents |
|---|---|---|
| `registry.py` | 986 | `viewers.json` manifest under `~/.cyoa_downloader/offline_viewers`; register/unregister (zip/rar); bundled auto-registration; ICC Plus sub-viewer extraction; runtime-family detection (project/HTML/archive); variant/quality ranking; `get_viewer_for_site`, `get_viewer_recommendations` |
| `injector.py` | 1151 | `_apply_offline_viewer`: extract viewer, then inject using A) template markers (Remix), B) `{DEFAULT_STATE}` balanced-brace splice into app.js (ICC Plus), C) fetch() rewrite or a file:// fetch interceptor; `_localize_preserved_index_assets` (needed by itch HTML5); font-link injection; RAR via `rarfile`/system tar |
| `modernizer.py` | 1716 | `analyze_site`, template resolution, `modernize_site` (backup to `__original_site__`, overlay template, preserved index merge, interceptor externalize/dedupe, file-protocol loader patches), `modernize_collection` (CLI `--modernize-library`) |
| `iccplus.py` | 202 | payload/interceptor builders, `_unique_folder`, ICC Plus viewer-config into HTML |
| `archive_store.py` | 17 | shim |

Callers: orchestrator (JSON viewer output at 802-980; website modernization at 744), cli (`--modernize-library`, `--viewer-collection`, `--modernize-output`, bundled registration), cyoa_manager serve folder, itch_offline, GUI.

## Proposed Rust layout
New module `cyoa_integrations::offline_viewers` with `registry.rs`, `inject.rs`, `iccplus.rs`, `modernize.rs`. No new crates except possibly RAR (see decision R1).

## Stages (each: Python fixture first, then Luna implements, Claude checks gates, Sol reviews where noted)
- **V1 registry (pure + manifest I/O):** manifest load/save with Python keys, safe names/paths, family detection, variant/quality ordering, `get_viewer_for_site`. Fixture: a Python dump over sample manifests and projects. The same `viewers.json` is read by both apps.
- **V2 injector + iccplus:** the three strategies, `_localize_preserved_index_assets`, font links. Fixture: Python output trees for synthetic Remix, ICC Plus and fetch viewers, compared byte-for-byte. **Sol review** (archive extraction = zip-slip surface).
- **V3 wiring:** orchestrator JSON-viewer output (settings `offline_viewer_json_enabled`, `offline_viewer_preferred_id`), CLI flags, itch HTML5 ZIP prep (F-041), CYOA Manager serve folder (F-038). Parity check against Python on `e2e_site` with a test viewer.
- **V4 modernizer:** `analyze_site`, `modernize_site`, `modernize_collection` + CLI `--modernize-library`; website modernization hook (`offline_viewer_website_enabled`). Fixture: Python before/after trees. **Sol review.**
- **V5 GUI:** viewer settings panel (register, unregister, preferred viewer, toggles).

## Decisions needed
- **R1 RAR viewers:** (a) shell out to system `tar` like the Python fallback (no new crate; recommended), (b) the `unrar` crate (C library, not pure Rust), (c) drop RAR and document it.
- Order: V1 → V2 → V3 first (unblocks itch and the Manager), V4 → V5 after.

## Test isolation
Viewer tests set `USERPROFILE`/`HOME` to a temp dir, so the real `~/.cyoa_downloader/offline_viewers` is never touched.
