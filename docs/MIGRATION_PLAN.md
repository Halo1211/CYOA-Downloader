# Staged migration plan (approved 2026-11-18)

Status 2026-11-21: all stages implemented; progress in `docs/MIGRATION_PROGRESS.md`, remaining gaps in `docs/RUST_PREVIEW_GUIDE.md`.

Principles:
- The Python app stays runnable and is the reference implementation.
- Rust lives under `rust/`.
- Each stage passes the quality gates, matches the golden files and gets a Sol review where the risk calls for it.
- Commits stayed local during the migration; the branch is published through a pull request into `main` (owner decision, 2026-11-21).

| Stage | Scope (feature IDs) | Main agent | Review | Exit criteria |
|---|---|---|---|---|
| 0 | Workspace skeleton, CI-equivalent script, Python golden-fixture exporter (`tools/export_fixtures.py`) | Luna | Claude | Gates are green on an empty workspace; fixtures generated |
| 1 | Core: paths, URL policy, atomic I/O, ZIP validation, settings/secrets, logging (F-001…F-007, F-054) | Luna (+MiMo proposals) | Sol (paths, ZIP, URL policy) | Property tests; fixture parity; settings round-trip with real settings files |
| 2 | Storage: cache, history, resume, SQLite checkpoints (F-008…F-011) | Luna | Claude | Reads Python-written files; Python reads Rust-written files |
| 3 | Network: client, admission, retry, throttle, proxy, DNS, VPN guard (F-012…F-018) | Luna | **Sol** | wiremock suites: redirects, 429/5xx, cancellation, byte caps, SSRF |
| 4 | Project and embed/ZIP pipeline plus CLI MVP (F-020…F-025, F-032, F-034…F-036, F-047 partial) | Luna | Sol | **First end-to-end milestone:** the CLI produces byte-identical embedded JSON and ZIP outputs for the fixture sites |
| 5 | Website archiving, Classic and Smart (F-026, F-027, F-029, F-030, F-033) | Luna | Sol | Folder trees and rewrite results match Python within approved differences |
| 6 | External tools and browser (F-019, F-028, F-031, F-040…F-042) | Sol (CDP) / Luna | Claude | Job-Object cleanup; SEC-1 policy enforced in the browser |
| 7 | Offline viewers, CYOA Manager, preview server (F-037…F-039) | Luna | Sol | SEC-2 fixed; modernizer outputs match fixtures |
| 8 | Diagnostics, update check, AI, plugins decision (F-043, F-044, F-051, F-052) | Luna | Claude | |
| 9 | GUI (egui): prototype gate first, then queue, log, settings, panels (F-048…F-050, F-053) | Luna | Sol (threading) | Responsiveness benchmarks beat the Python baseline (BENCHMARKS.md) |
| 10 | Packaging, benchmarks, release docs, clean publication directory (F-055) | Claude | Sol (release audit) | Release checklist in the master prompt §24 |

Each stage is approved separately before Luna's write-enabled calls start.

## Risks

1. **Scope:** about 60k lines, and parity is hard for HTML rewriting and viewer modernization.
2. **Browser features:** no pure-Rust Playwright equivalent exists. CDP integration is significant work.
3. **The GUI gain is unproven:** Python already has heavy optimization (see PROJECT_AUDIT §4).
4. **Local models are unreliable.** Mitigation: limit them to narrow tasks with checkable answers.
5. **Golden-file fidelity** depends on deterministic Python output (timestamps, ordering).
