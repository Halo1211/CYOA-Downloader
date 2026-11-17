# Decision log

| ID | Date | Decision | Rationale | Rejected alternatives |
|---|---|---|---|---|
| DL-1 | 2026-11-18 | Use the egui/eframe GUI (prototype gate in Stage 9) | Dense tool UI, `show_rows` virtualization, single exe, no WebView | Iced (more boilerplate for many panels), Slint (DSL and licensing), Tauri (JS/TS frontend and WebView2) |
| DL-2 | 2026-11-18 | Put the Cargo workspace under `rust/`, with 8 crates | Keeps Python runnable; clean layering | One crate (poor isolation) |
| DL-3 | 2026-11-18 | Keep every feature. Playwright and Selenium both move to one CDP backend; cookie import and Cloudflare get Rust equivalents | Owner: "keep every feature; identical or better" | Dropping Selenium/cookies/cloudscraper |
| DL-4 | 2026-11-18 | Python plugins move to external-process plugins speaking JSON | Python cannot run inside a pure Rust binary | Embedding Python (needs a runtime) |
| DL-5 | 2026-11-18 | Store secrets in the keyring by default | Fixes SEC-3 | Plaintext default |
| DL-6 | 2026-11-18 | Generate golden fixtures from the live Python functions (`rust/fixtures/export_*.py`), with machine paths replaced by `<ROOT>` | Parity evidence comes from the reference implementation, not from agent claims | Hand-written expectations |
| DL-7 | 2026-11-18 | Claude pre-fetches crates; agents build with `--offline` | Luna's sandbox cannot reach crates.io (`SEC_E_NO_CREDENTIALS`) | — |
| DL-8 | 2026-11-18 | Path helpers reject **any** link or junction component inside the output root (stricter than Python) | Sol review: dangling links bypassed the existence-based check | Resolve-then-check (TOCTOU-prone) |
| DL-9 | 2026-11-18 | `copytree_merge_safe` returns a `CopyReport` (copied plus skipped with reasons) instead of logging | Observable, testable; replaces Python's logger warnings | Silent skip |
