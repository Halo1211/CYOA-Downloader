# Environment status

Recorded 2026-11-18 during Phase 0. All values come from commands run in this session.

## Host

- Windows 11 Pro 10.0.22631. The owner reports an i7-13620H, an RTX 4070 Laptop GPU (8 GB) and 32 GB RAM; this session did not measure the hardware.
- Repository: `D:\Projects\CYOA-Downloader`. Branch: `migration/python-to-rust`. The working tree was clean at the start.
- Remote: `origin` points to GitHub. **Never push.** The remote was not changed.

## Toolchain (versions reported by the tools)

| Tool | Version | Status |
|---|---|---|
| cargo / rustc | 1.100.0 | OK |
| rustfmt | 1.11.0 | OK |
| clippy | 0.1.100 | OK |
| rust-analyzer | 1.100.0 | OK |
| cargo-nextest | 0.9.152 | OK |
| cargo-audit | 0.23.0 | OK |
| cargo-deny | 0.21.0 | OK |
| cargo-llvm-cov | 0.9.1 | OK |
| Python | 3.12.10 | OK |

The repository has no Cargo workspace yet.

## Python baseline

`python -m pytest -q -p no:cacheprovider` passed: **1633 passed, 21 skipped, exit code 0, 58.96 s.**
The skipped tests need a desktop, Playwright Chromium, or live opt-in environment variables (`CYOA_GUI_SMOKE`, `CYOA_RUNTIME_SMOKE`).

## hybrid-team MCP (`team_status`)

- Codex: ChatGPT login detected. Luna is `gpt-6-luna`; Sol is `gpt-6.1-sol`.
- Ollama: `qwen3.5:4b-q4_K_M`, `mimo-v2.6:9b-q4_K_M`, plus the unused `medgemma:4b` and `qwen2.5vl:7b`.
- Local context: 8192 tokens.
- **Codex writes enabled: True.** The owner must approve every write-enabled call (`luna_implement`, `sol_implement`).
- `team_status` does not show a paid-API fallback flag, so this session could not verify that setting.

## Agent availability

| Agent | Smoke test | Audit task | Observed quality |
|---|---|---|---|
| Luna | OK | Read-only repository audit | Good; citations were checked and accurate. The output was truncated in section 6. |
| Sol | OK | Security and architecture review | Excellent; all four sampled citations were verified. |
| MiMo | OK (code had bugs) | Python→Rust semantic hazards for `_safe_rel_path` | **Failed.** It repeated itself past the limit and made factual errors (claimed Rust classes are case-insensitive and that `trim()` equals `str.strip()`). |
| Qwen | OK (fair) | Module tier classification | **Unreliable.** It classified by module name; at least 8 of 51 rows were wrong. |

## Skills

The project skills are present in `.claude/skills`: feature-parity, gui-performance, hybrid-orchestrator, migrate-to-rust, rust-best-practices and rust-engineer. Superpowers is installed. Context7 MCP is available for checking crate APIs during implementation.
