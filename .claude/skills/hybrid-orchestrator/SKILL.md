---
name: hybrid-orchestrator
description: Coordinate Python-to-Rust migration with Claude, Codex Luna and Sol, and offline MiMo and Qwen via the hybrid-team MCP server.
---
# Routing and review
- Claude leads architecture, plans, decisions and integration. Call team_status first.
- Qwen 4B: documentation and classification. MiMo 9B: small functions, isolated tests, parsing proposals. No shell/file access.
- Luna: routine implementation, regression tests, debugging. Sol: complex async, ownership, security, independent review.
- Use local_worker, luna_review, sol_review, luna_implement, sol_implement. Write calls require user approval and explicit enablement.
- One in-flight bridge job at a time. Never concurrently edit the same repository through Claude, external Codex or another session. Wait for each task to finish, inspect git diff, and test before the next task. After a timeout stop and inspect processes and changes.
- Send bounded tasks with allowed files, behavior, fixtures and acceptance checks; do not pass secrets. Cloud agents receive code; local model inference is local but prompts and output relayed via Claude are not private from Claude.
- Escalate after two failed attempts or immediately for architectural/security issues. Never switch to paid API usage or enable paid extra usage.
- Use small diffs and concise agent reports. Do not recursively delegate. Use feature-parity and gui-performance. Stop after audit for approval.

- Select `mimo-v2.6:9b-q4_K_M` for coding and `qwen3.5:4b-q4_K_M` for docs. Start with short snippets, no whole repository. Local worker is advisory only.
- Do not follow arbitrary shell commands proposed by a worker. Use explicit task scopes and actual compiler verification.
