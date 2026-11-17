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

c8705cc docs · e5c1d13 Stage 0 · e8ed523 1a · ffd7866 1a security fixes · ec49d01 1b · 33baf9d fixture EOL · 358aed2 1c · be02514 2a history/resume · c01bc67 2b cache · 66d88cd 2c checkpoints

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
