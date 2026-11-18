# Security review: Python baseline

Source: Sol's independent static review, 2026-11-18. Claude checked the citations marked ✔ against the source. Nothing was run, so exploitability was not tested. Paths are relative to `cyoa_downloader_app/`.

| ID | Severity | Status | Finding | Location | Rust requirement |
|---|---|---|---|---|---|
| SEC-1 | HIGH | Confirmed from source ✔ | The browser backend calls `page.goto()` with no internal-host or SSRF interception. A public page can make the browser request loopback or RFC1918 addresses, which bypasses the native-fetch policy | `network/browser_owner.py:157` ✔; `network/runtime_capture.py:486`; compare `download/website.py:1543` | Apply one destination policy everywhere: navigation, redirects, subresources, workers, and helper executables |
| SEC-2 | MEDIUM | Confirmed (needs links) ✔ | The preview server (bound to 127.0.0.1, random port ✔) inherits `SimpleHTTPRequestHandler`. It has no resolved-root containment, keeps directory listing on, and sends `Access-Control-Allow-Origin: *` ✔. Symlinks or junctions can expose files outside the folder | `gui/app.py:11936-12131` ✔ | Check the canonicalized root for every route, reject reparse-point escapes, turn off directory listing, restrict CORS |
| SEC-3 | MEDIUM | Confirmed ✔ | A Discord token stored in the legacy keyring is copied into plaintext `settings.json` on the next save (intentional compatibility code). Plain API-key modes also exist | `config/settings.py:371-391` ✔ | Keep secrets in the keyring by default. Plaintext only by explicit user choice. **Owner decision D5** |
| SEC-4 | MEDIUM | Confirmed (Sol) | Some paths have no byte ceiling: the HTTP/2 deep scan buffers whole bodies, the streaming writer has no max size, browser bodies are fully read, and RAR helper stdout is fully captured | `download/image_pipeline.py:1542`, `download/package.py:718`, `network/browser_owner.py:172` ✔, `offline_viewers/injector.py:70` | Enforce incremental byte, file, job, disk and time budgets |
| SEC-5 | INFO | Hypothesis | ZIP validation is strong (traversal, device names, case collisions, links, expansion are all handled), but the early metadata cap can be skipped for some ZIP64 records and for already-open archives | `core/archive.py:42,61,111`; `core/paths.py:107` ✔ | Port the rules, then fuzz them with `proptest` |
| SEC-6 | INFO | Suspected | DNS rebinding window between the policy check and the connection | `integrations/ai_core.py:326` | Resolve once, then connect to the vetted IP |
| SEC-7 | LOW | Confirmed ✔ | Chromium is launched with `--no-sandbox` | `network/browser_owner.py:113` ✔ | Do not pass `--no-sandbox` |
| — | INFO | Confirmed | No `verify=False` and no `shell=True` found ✔. Subprocesses use argument arrays. Filenames are sanitized for reserved Windows names ✔ | — | Keep it that way |

No CRITICAL findings.

## Policy carried into Rust

- Never disable TLS verification.
- Treat downloaded HTML, JSON and agent output as untrusted data.
- Do not commit secrets.
- Preview content must never reach privileged app IPC.

## Session 4 — Sol review of output stage (run/output/reports/cli)
9 findings; fixed by Claude (Luna writes failed):
- High: no-clobber publication (owned temp + hard_link/create_new; concurrent writers tested).
- High: exclusive run temp dir with RAII cleanup (also on future abort).
- Medium: pre-existing `.part` files are never deleted; unique pid+counter temp names.
- Medium: ZIP entry names validated (no `..`, absolute, drive, backslash).
- Medium: filenames over 200 bytes bounded at char boundaries (normal names unchanged → Python parity).
- Medium: output stage runs in spawn_blocking with cancellation before each publication.
- Medium: CLI keeps the real result if Ctrl-C registration fails.
- Open (deferred): aggregate run byte budget (KI-R5).
