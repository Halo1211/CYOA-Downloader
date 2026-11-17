# Agent task ledger

### A-000 Smoke test
Assigned agent: all · Status: DONE · Results: all connections OK. MiMo's code had overflow and test bugs; Qwen made factual slips; Luna and Sol were accurate.

### A-001 Repository audit
Assigned agent: Luna (luna_review) · Objective: inventory the CLI, features, dependencies, concurrency, GUI blocking and persistence · Allowed files: none (read-only) · Status: DONE
Implementation summary: report delivered; it was truncated in §6 (persistence), which Claude completed by direct inspection.
Tests performed: none (read-only) · Reviewer: Claude spot-checked 6 citations; all accurate.
Outstanding issues: none.

### A-002 Security and architecture review
Assigned agent: Sol (sol_review) · Allowed files: none · Status: DONE
Implementation summary: 7 security findings, a portability triage and an architecture proposal.
Reviewer: Claude verified 4 citations (`browser_owner.py:113,157`, `settings.py:371`, `app.py:11936`); all accurate.

### A-003 Semantic hazards of `_safe_rel_path`
Assigned agent: MiMo · Status: FAILED (advisory output rejected)
Issues: it repeated itself past the 15-bullet limit and made false claims (Rust regex classes "case-insensitive", `trim()`≡`str.strip()`, a literal-NUL claim).
Next action: Luna gets this task during Stage 1 together with property tests against Python.

### A-004 Module tier classification
Assigned agent: Qwen · Status: DONE (low quality) · Reviewer: Claude found 8 or more misclassifications. Not used verbatim.

### A-005 Python test baseline
Assigned agent: Claude · Result: 1633 passed, 21 skipped, exit code 0, 58.96 s.

### A-006…A-020 Overnight implementation (summary; details in OVERNIGHT_REPORT.md)

| Task | Agent | Outcome | Reviewer / verification |
|---|---|---|---|
| Stage 0 skeleton | Luna (implement) | OK. crates.io was blocked in the sandbox; Claude ran the gates | Gates passed |
| 1a paths/url | Luna (implement) | OK after Claude's fixture fix | Sol review found 2 HIGH issues; fixed by Luna |
| 1b atomic/ZIP | Luna (implement) | Partial. Claude truncated `archive.rs` by mistake; Luna's recovery draft didn't compile; Claude fixed it | 9 Python verdicts |
| 1c settings | Luna ×2 failed → **escalated to Sol** | Sol 39/40; Claude corrected one wrong expectation against Python | 30 golden cases |
| 2a history/resume | Luna (review→code) | Needed `_metadata_id`, job key and mode fixes from Claude | Byte parity |
| 2b cache | Luna (review→code) | Dropped the link guards and had borrow errors; Claude fixed | Unit tests + format |
| 2c checkpoints | Luna draft truncated; **Claude wrote it** | DB-name parity bug in the draft (`\\?\`) | Python name/payload fixture |
| 3a admission/retry | Sol (review→code) | Good; replies truncated; Claude wrote throttle | Async tests |
| 3b policy/headers | Sol (policy) / Claude (headers table) | Good | 66 Python fixture cases |
| 3c HTTP client | Sol (review→code) | Good; one Windows-specific test issue fixed | wiremock tests |
| 4a parse.py | Luna (review→code) | Good; Claude fixed clippy and documented improvements | 18×5 parity |
| 4b discover helpers | Luna (review→code) | Wrong SSRF helper and backreference regex; Claude fixed | Python parity |

## Lessons for routing

- Luna's and Sol's sandboxes cannot write files and cannot reach crates.io or Python. The working pattern is review→code→Claude saves and verifies.
- Always regenerate golden fixtures from live Python. Agents' hand-written expectations were wrong twice.
- Ask for one file per call; Sol and Luna replies truncate at about 14–15k characters.

- MiMo and Qwen need tasks whose answers can be checked mechanically, such as a single function with explicit expected values, compiled and tested by Claude or Luna.
- Keep requests short (an 8k context) and cap the output length.
