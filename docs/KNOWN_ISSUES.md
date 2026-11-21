# Known issues (Python baseline)

| ID | Area | Issue | Source |
|---|---|---|---|
| KI-1 | Security | SEC-1…SEC-7 | SECURITY_REVIEW.md |
| KI-2 | GUI | Pillow icon and logo decoding runs on the Tk thread at startup (low impact) | `gui/app.py:341`, `gui/assets.py:64,76` |
| KI-3 | GUI | Remaining roadmap items: queue copy and membership scans, widget and font startup cost, output and cookie preparation | `docs/OPTIMIZATION_ROADMAP.md` |
| KI-4 | GUI (suspected) | History and library loading when a panel opens may block | `gui/final_behaviors.py:315`; needs measurement |
| KI-5 | Memory | Report models grow with the result count; cache JSON merge scales with index size | OPTIMIZATION_ROADMAP |
| KI-6 | Network | No single retry budget across connection, body, auth and backend recovery | OPTIMIZATION_ROADMAP |
| KI-7 | Structure | Global mutable compatibility layer (`compat.py`, `runtime/surface.py`) | audit |
| KI-8 | Tests | 21 tests are skipped (GUI, Playwright and live). Browser and GUI paths have no automated baseline | ENVIRONMENT.md |

## Session 4 (Rust engine)
- **KI-R1 memory:** FIXED (session 5) — bodies are spooled to the run temp dir and the embedded JSON is streamed to a file (`Replacement::Media`). Peak working set at 300×50 KB `--both`: 98 MB → 28 MB (Python: ~60 MB). One body per worker is still held briefly in memory (for the cache write).
- **KI-R2 transport retries:** FIXED (session 4) — 3 transport retries with urllib3 backoff 0/2/4 s before "fetch_response returned None"; read-phase (mid-body) retries still not ported.
- **KI-R3 error strings:** other-4xx/TLS failure messages differ from requests' wording.
- **KI-R4 blocking resolver:** `FetchCtx.resolver` (SSRF cross-origin check) is synchronous on runtime threads.
- **DEFERRED in fetch_one:** gallery-dl, headless browser, cookie session, Discord refresh, MediaWiki File: resolution, VPN guard, yt-dlp. (s8: headless browser fallback implemented, F-028; gallery-dl, Discord refresh, MediaWiki and VPN were done in s6; cookie session remains deferred with F-045.)
- **KI-R5 aggregate quota:** only a 512 MiB per-asset cap; no run-wide byte budget (Sol Medium, deferred).

## Session 6
- FIXED: Rust `.txt` reports were LF while Python writes CRLF on Windows (text mode). Now `reports::os_text`. The old e2e script compared with `grep -v` + `diff` and would have reported OK even if both files were missing; it now requires both files and compares byte-exact.
- FIXED: Rust always wrote `failed_assets.txt`; Python appends to an existing `backup_report.txt` and removes stale `broken_assets_report.html` (`write_asset_failure_summary`).

## Browser runtime capture: no destination policy on page subrequests (same as Python)
Chromium page subrequests are only filtered like Python `route_request`: non-GET/HEAD and off-page navigation are blocked during safe interaction. Internal or loopback hosts requested by a page's own scripts are not blocked, in Python or in Rust. Changing this would diverge from Python; it needs owner approval. The `blocked` list is now capped at MAX_TRACKED_REQUESTS (Rust only). Self-reviewed 2026-11-21; the independent Sol review is still pending because of the Codex usage limit.

## 2026-11-21 (real-site testing)
- FIXED: cyoa.cafe resolution (incomplete `validate_candidate` port), ICC/Pure pre-resolution of `cyoa.cafe/game/<key>`, float precision (`float_roundtrip`), integrity and offline-viewer regex hangs, faproulette 429s (native TLS, retries), cross-origin assets failing after browser fallback.
- Python bugs found that Rust does not have: Pure Website crash on an empty response body (`OSError: Downloaded response body is empty`), settings export leaks URL credentials, itch.io auth test fails for valid keys (old endpoint), Selenium Manager files (`se-metadata.json`, `chromedriver/`) written into the output folder.
- Expected differences: duplicate-image names in ZIPs (Rust deterministic, Python thread-order dependent), Google Fonts woff2 vs ttf (User-Agent dependent), runtime `?time=` hashes, YouTube track availability per run.
- Deep scan tries a few more alternative paths than Python, so `failed_assets.txt` can list more "not found" probes (no real asset missing).
- Rust `--self-test` covers 15 checks against Python's 42 (Python's extra checks exercise Python internals).
