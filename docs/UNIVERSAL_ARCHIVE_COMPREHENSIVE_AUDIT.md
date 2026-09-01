# Universal Archive Comprehensive Audit

This document records the integration areas reviewed for Classic, Smart, and
Browser archiving. The objective is to preserve historical downloader behavior
while adding bounded support for JavaScript websites.

## Resolved findings

1. **Manually edited settings could corrupt types and ranges.** The loader now
   normalizes booleans, integers, case-insensitive enums, AI temperature, and
   null values.
2. **Settings enums did not match CLI and GUI choices.** Gallery-DL,
   FlareSolverr session/proxy values, and AI `env` storage are preserved.
3. **A depth of zero became 30.** Policy normalization, CLI defaults, and GUI
   initial values now preserve `archive_max_depth=0` as entry-only archiving.
4. **Route names could collide or be invalid on Windows.** Path components are
   sanitized, Windows device names are protected, Unicode is retained, and
   collisions receive stable hashes.
5. **Malformed IPv6 URLs could stop a crawl.** Canonicalization, parsing,
   rewriting, and resume paths now ignore malformed values safely.
6. **A page cap looked like successful completion.** The manifest records
   `route_limit_reached` and `remaining_queued_routes`.
7. **Resume trusted damaged manifests and unsafe paths.** Recovery rebuilds a
   safe manifest, validates local paths, ignores missing files, and can recover
   unresolved relative links.
8. **Runtime capture missed some assets.** Detection recognizes image, audio,
   video, font, CSS, JavaScript, JSON, WASM, and useful file extensions when a
   server provides missing or incorrect MIME types.
9. **Interaction behavior was unclear.** The legacy interaction setting remains
   compatible, while current Browser behavior is bounded and safe interaction
   never performs blind form submission.
10. **Concurrent website localization treated an in-progress sentinel as a
    path.** Cache reuse now accepts only real local path values, preventing the
    Teen Titans `os.path.isfile(object)` failure.
11. **Settings network testing requested a third-party favicon.** Validation is
    now offline and inspects only configuration and local interfaces.
12. **DoT could weaken hostname verification.** DoT now requires a hostname,
    bootstraps its address separately, and retains TLS server-name validation.

## Compatibility invariants

- Classic remains available as the historical path.
- Smart and Browser run only when explicitly selected or chosen by Auto.
- Crawling is bounded by origin, story scope, page count, and depth.
- Runtime assets pass through the normal download pipeline.
- `settings.json` remains a flat object; `_meta` and `_section_...` fields do not
  become runtime state.
- Logs and portable exports redact secret values.
- VPN policy is an application guard and never claims to create a tunnel.

## Verification checklist

Run from the repository root:

```powershell
python -m pytest -q
python -m pytest -q -W error
python -m compileall -q cyoa_downloader.py cyoa_downloader_app tests tools
python cyoa_downloader.py --self-test
python cyoa_downloader.py --dependency-check
```

Historical parity checks may report deliberately additive archive parameters.
An additive signature difference is not a removed API as long as required names
and earlier calling conventions remain available.

## Real-site patterns represented by tests

- CYOA.CAFE catalog records and linked creator viewers;
- Teen Titans concurrent asset localization and cache reuse;
- Hypnosis Arena JavaScript image arrays and dynamic base paths;
- large route stories, Next.js image optimization, and lazy/runtime assets;
- Discord CDN refresh and local project-reference rewriting;
- proxy, custom DNS, DoH/DoT, and fail-closed VPN guard behavior.

Archive output should be tested through a local HTTP server. `file://` is not a
reliable indicator for modern applications because of module, fetch, CORS, and
service worker rules.

## Current verification result

- Full offline pytest suite: 429 passed, 7 skipped.
- Focused network, GUI, and universal archive suite: 90 passed.
- Dependency diagnostics: 30 of 30 detected capabilities.
- No public website or DNS resolver probe is used by Settings validation.
