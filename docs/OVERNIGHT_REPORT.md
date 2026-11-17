# Overnight report (updated continuously)

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

c8705cc docs · e5c1d13 Stage 0 · e8ed523 1a · ffd7866 1a security fixes · ec49d01 1b · 33baf9d fixture EOL · 358aed2 1c

## Blockers / owner attention

- **Luna sandbox:** no network and no Python, plus intermittent write denials. Workaround: Claude pre-fetches crates and runs the Python exporters.
- **casefold:** ZIP duplicate detection uses Rust `to_lowercase` where Python uses `casefold()`. They differ for characters like ß; tracked as a parity gap.
- **Settings gaps (Sol):** arbitrary-size integers, non-finite numbers, unpaired surrogates and some numeric-string forms are not matched exactly.
- **Native keyring:** not tested against Python's real Credential Manager entries. Needs a manual check on this machine before release.

## Next actions

1. Stage 2: cache index, history, resume, SQLite checkpoints (needs `rusqlite` pre-fetched).
2. Stage 3: network (reqwest, admission 20/12, retry) with a Sol review.
3. Stage 4: project discovery and embed/ZIP pipeline, plus CLI MVP (first end-to-end parity milestone).
