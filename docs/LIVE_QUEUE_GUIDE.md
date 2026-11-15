# Real queue downloads and offline checks

Run the CSV queue with separate download processes and a checkpoint per URL:

```powershell
python tools/run_live_queue.py --queue "E:\cyoa_queue.csv" --output "E:\Download" --jobs 2 --timeout 5400
```

Completed viewers use `Title/index.html`, without generated hash suffixes or
an extra `archive` directory. Authored tags such as `[CYOA]` remain part of the
title. Different URLs with the same title use `Title (2)`, `Title (3)`, and so on.
Internal URL IDs stay in `_audit`; staging and process logs stay under
`_audit/work`. Existing unrelated title folders are never overwritten.

Repeated URLs share one download. Each unique URL owns a named folder and an
isolated output lease. Re-run the same command to skip completed downloads and
retry failed/interrupted jobs. Previous partial folders are retained. The image
cache can reuse assets fetched before an interruption. A job has a 30-minute
timeout by default; `--timeout` sets seconds per job. Timeout cleanup includes
the browser processes owned by that job on Windows.

The worker uses Auto archiving, bounded safe interactions, and the registered
offline viewers. Its archive-policy overrides apply to that worker. AI calls
are disabled for this deterministic batch workflow. The network configuration
and FlareSolverr endpoint come from the normal application settings.

`index.html` lists the library. `_audit/queue_status.json` and individual JSON
checkpoints record download status, process IDs, output entries, and duration.
`_audit/retry_queue.csv` contains unfinished URLs. The input CSV is never edited.
Download completion means the CLI produced an entry page; it does not guarantee
every remote dependency or backend feature works offline.

To reorganize older `Title [ID]/archive/index.html` results without downloading
again, preview the complete move plan and then apply it:

```powershell
python tools/organize_live_library.py --output "E:\Download" --report layout-plan.json
python tools/organize_live_library.py --output "E:\Download" --apply --report layout-result.json
```

The organizer requires completed checkpoint entries and no running queue jobs.
It moves each viewer directory intact within the same volume, verifies relative
filenames/file identities/sizes/timestamps, updates checkpoints and catalogue
links, and retains old metadata/logs under `_audit/layout-backups`. It does not
edit game assets or strip authored title tags. Collisions receive readable
numeric suffixes, and a failed move/metadata commit rolls back prior moves.

Check completed viewers with all outside network access blocked:

```powershell
python tools/audit_live_viewers.py --output "E:\Download"
```

Each test serves one viewer at its own HTTP root, matching the app's **Serve**
feature, then renders, scrolls, and tries bounded safe opening controls. Reports
are `_audit/<URL ID>.viewer.json`. Use `--force` to repeat checks after repairs,
or `--id <URL ID>` to select a specific entry. These are smoke tests rather than
exhaustive checks of every story branch. External telemetry can appear in the
reports even when the story itself works.

Repairs for archives created before the latest discovery improvements:

```powershell
python tools/repair_tiled_archives.py --output "E:\Download"
python tools/repair_live_viewers.py --output "E:\Download"
```

The downloader now enumerates the image-overlay viewer's authored tile metadata,
including shared page dimensions and animated-page files. It also follows
literal language alternatives in `document.write()` data-script loaders and
recursively saves imports/images/fonts referenced by project `customCSS`.

Perchance applications whose public source manifest contains ordinary HTML/JS
are exported from the versioned source data, including every uploaded source
file. This avoids archiving only the editor/content-warning shell. The raw
source record and overwritten source files are retained. Templates requiring
Perchance expressions or imported generators keep the normal archive fallback.

Plus 2 viewer conversion supplies missing collection defaults for older exports
without replacing authored values or editing the original project backup.
Browser capture recognizes staged opening controls, rechecks the DOM after each
click, and carries matching HTTP-session cookies/user agent into Chromium.
State-changing requests remain blocked after automated interaction starts.

Opening-gate waits end early only when the next safe control is available,
the changed page is stable, and pending requests have finished. The last gate
retains the full settling budget to capture delayed assets. A flat page skips
the extra scroll-reset wait. GUI event processing uses short time slices so
large downloads do not hold the interface through a full callback batch.

`python tools/benchmark_auto.py` compares fixed/adaptive waits using real
Chromium and the same four-gate fixture, including a delayed final asset.
`python tools/benchmark_runtime.py` measures scanning and scheduling overhead.
Reported timings depend on machine load; these are not network-speed promises.
