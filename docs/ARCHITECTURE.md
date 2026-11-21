# Rust architecture (approved 2026-11-18, implemented)

Status 2026-11-22: implemented, with Rust workspace at repository root and Python reference under `old/`. Additions since approval: `cyoa_net::browser_relay` (loopback relay in front of Chromium: destination policy and proxy credentials), native TLS on Windows (DL-10), external process plugins (DL-4, DL-14). Decisions: `docs/DECISION_LOG.md`.



## GUI framework comparison

| Criterion | egui/eframe | Iced | Slint | Tauri |
|---|---|---|---|---|
| Pure Rust | Yes | Yes | Yes (DSL; GPL/royalty-free licensing terms apply) | No (WebView plus a JS/TS frontend) |
| Large virtual lists and logs | `ScrollArea::show_rows`; good | Needs manual work | Good (`ListView`) | Good (web virtualization) |
| Integration with background work | Channels drained each frame plus `request_repaint` | Built-in `Task`/`Subscription` | Event-loop callbacks | IPC |
| Image previews | Textures; simple | OK | OK | Native web |
| Many settings panels | Fast to write | More boilerplate | Designer DSL | Web |
| Accessibility | AccessKit (partial) | Limited | AccessKit | Best (browser) |
| Native dialogs | `rfd` | `rfd` | `rfd` | Built in |
| Windows packaging | Single exe | Single exe | Single exe | Needs the WebView2 runtime |
| Idle CPU | Repaints only on input or request | Retained mode, low | Low | WebView process |

**Recommendation: egui/eframe.** It fits a dense tool UI with a large queue, a fast log and many settings, and it builds as a single exe. Iced is the alternative if the owner prefers retained, message-driven state.

**Prototype gate:** within the first two weeks of GUI work, measure IME/Unicode text input, high-DPI rendering, idle CPU and a 100k-row queue before committing.

## Workspace layout

The Python reference remains runnable under `old/`. Rust is the primary implementation; its workspace is at the repository root.

```
Cargo.toml                 # root workspace manifest
crates/                    # eight Rust crates
fixtures/                  # golden files exported from Python
scripts/                   # parity, packaging, and release utilities
old/                       # Python reference application and tests
```
## Concurrency and data flow

```
 UI thread (eframe)                 Tokio runtime (dedicated thread)
 ───────────────────                ─────────────────────────────────
 AppState ──Command (bounded mpsc 256)──▶ Controller
    ▲                                      │ spawns Jobs (JoinSet, bounded by settings.workers ≤ 32)
    │                                      ▼
    └──Event (bounded mpsc, coalesced)◀── Job ─▶ Admission(global 20 / host 12 semaphores, fair)
       progress ≤10 Hz per job,            │      ─▶ reqwest (timeouts, capped streaming)
       terminal events never dropped       ├─▶ spawn_blocking pool: parse, hash, zip, image decode
       ctx.request_repaint()               └─▶ storage writer (atomic temp+rename)
```

- **Cancellation:** a root `CancellationToken`, with a child token per job and per request. External helper processes run in Windows Job Objects so the whole process tree is killed.
- **Retry:** bounded exponential backoff with jitter, one budget per asset, honoring `Retry-After`, the same status codes as `network/retry_policy.py`, and no infinite loops.
- **Backpressure:** every channel and queue is bounded. The log is a ring buffer of 10k lines. The image texture cache is an LRU with a byte cap.
- **Errors:** `thiserror` enums in the library crates and `anyhow` only in the binaries. No `unwrap` on runtime paths.
- **Unsafe:** none planned. Win32 calls go through the `windows` crate's safe wrappers. Any `unsafe` needs Sol's review.

## Python-semantics hazards (parity tests required)

- Python `str.strip()` vs Rust `trim()`. Python also strips U+001C–U+001F.
- `urllib.parse.unquote` replaces invalid UTF-8 with U+FFFD.
- `re` lookarounds have no `regex` equivalent; use `fancy-regex` in a few places or rewrite.
- `html.parser` vs `html5ever` produce different trees.
- JSON key order (`serde_json` `preserve_order`).
- Python's arbitrary-size ints: use checked arithmetic and no silent saturation.
- `os.path` on Windows: reserved names, trailing dots, long paths (`\\?\`).
- Case-insensitive file systems.

## Acceptance criteria (architecture)

1. The UI thread never performs network or file I/O over 1 ms or decodes images. CI enforces this with frame-time tests.
2. Queue scrolling with 100k rows stays at 60 fps on the reference laptop. To be measured.
3. Cancelling all jobs reaches the terminal state in under 1 s with no orphaned helper processes.
4. The `fixtures/` golden outputs are byte-identical to Python, or every difference is documented and approved.
5. `cargo fmt --check`, `check`, `clippy -D warnings`, `test`, `audit` and `deny` are all clean.
