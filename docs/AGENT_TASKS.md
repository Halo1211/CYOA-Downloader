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

## Lessons for routing

- MiMo and Qwen need tasks whose answers can be checked mechanically, such as a single function with explicit expected values, compiled and tested by Claude or Luna.
- Keep requests short (an 8k context) and cap the output length.
