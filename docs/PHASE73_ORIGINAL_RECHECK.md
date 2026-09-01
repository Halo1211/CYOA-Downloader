# Phase 73 Original Parity Recheck

This audit rechecked the refactored package against the original uploaded single-file script.

## Scope

- Original reference: `/mnt/data/cyoa_downloader(3).py`
- Refactored tree: `cyoa_refactor_phase69_72_legacy_deleted`
- Goal: detect bugs introduced by splitting modules and deleting `cyoa_downloader_app/legacy.py`.

## Checks performed

1. Full pytest suite.
2. CLI smoke checks:
   - `python cyoa_downloader.py --help`
   - `python cyoa_downloader.py --dependency-check`
   - `python cyoa_downloader.py --self-test`
3. Compile/import checks:
   - `python -m compileall`
   - import every package module via `pkgutil.walk_packages`
4. Original-vs-refactor compatibility audit:
   - all top-level original function/class names exist on the refactored facade;
   - call signatures match when ignoring annotation formatting;
   - key constants match exactly.
5. Representative behavior smoke comparison for pure helpers:
   - batch mode normalization/flag derivation;
   - URL/canonicalization/output-name helpers;
   - bytes decoding;
   - project payload detection;
   - formatting/path safety helpers.

## Results

- Pytest: `130 passed`
- CLI smoke: all return code `0`
- Self-test: `37/37 passed`
- Compile/import all package modules: pass, `110` modules imported, `0` failed
- Original callable compatibility:
  - checked callable names: `363`
  - missing names: `0`
  - signature diffs: `0`
- Key constant diffs: `0`
- Representative behavior smoke mismatches: `0 / 51`
- `cyoa_downloader_app/legacy.py`: absent/deleted

## Issues found and fixed during recheck

The recheck found no major runtime breakage, but it found three small facade-parity risks caused by re-export ordering after `legacy.py` deletion:

1. `_v46_default_progress_expanded` was being exported from the older v46 body instead of the final v46.2 replacement body.
2. `_v25_manage_offline_viewers` and `_v25_inject_into_viewer` were being exported through lazy injector wrappers instead of the final GUI patch body functions.
3. `_preview_token_valid` used parameter name `token`; original facade used `tok`, which matters for keyword calls.

Fixes applied:

- Re-pinned final GUI patch aliases in `gui/bootstrap.py`.
- Prevented `runtime/surface.py` from overwriting final v25/v46.2 patch symbols with transitional wrappers.
- Restored `_preview_token_valid(tok=...)` keyword compatibility.
- Added regression tests in `tests/test_phase73_original_parity_fixes.py`.
- Added `tools/audit_original_parity.py` for future original-vs-refactor audits.

## Remaining note

Some modules still have bridge helpers named `legacy()` for transitional compatibility, but they now resolve to `cyoa_downloader_app.runtime.surface`, not to a physical `legacy.py` file. No `cyoa_downloader_app/legacy.py` module is required at runtime.
