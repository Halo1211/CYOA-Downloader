# Audit Report — CYOA Downloader v1.0 Release

This report summarizes the stabilization and GitHub-release preparation work for v1.0.

---

## 1. Release scope

Primary goals:

- stabilize the existing downloader without changing the core download concept;
- preserve old internal data compatibility;
- rename user-facing Website Mode to ICC Mode;
- remove old Website CLI flags for consistency;
- prepare GitHub documentation and MIT licensing;
- document all major features in README and docs;
- include light/dark compatible logo assets.

---

## 2. Important compatibility decision

The CLI flags below are intentionally removed:

- `--website`
- `-W`
- `--website-folder`

The internal keys below remain preserved:

- `website_zip`
- `website_folder`

Reason: internal keys may already exist in CSV batch files, settings, cache data, and manifest files. Renaming them would create avoidable compatibility risk.

---

## 3. Feature inventory audited/documented

The documentation now covers:

- GUI and CLI usage;
- batch import TXT/CSV/XLSX/XLS/remote CSV/Google Sheets;
- embedded JSON, ZIP, Both, ICC ZIP, ICC Folder;
- Pure Website ZIP/Folder;
- CYOAP Vue ZIP/Folder;
- deep scan;
- image/audio/video/font detection;
- Google Fonts and direct font download;
- failed asset reporting;
- failed batch URL reporting;
- logging;
- settings/cache;
- userscript integration;
- local serve/preview;
- IntCyoaEnhancer bundled helper credit;
- AI Assist;
- Cloudflare/cloudscraper/FlareSolverr;
- proxy/DNS/BebasDNS/HTTP2;
- gallery-dl;
- yt-dlp;
- Playwright/Selenium headless fallback;
- CYOA Manager integration;
- itch.io optional backend;
- EXE build guidance.

---

## 4. Stabilization points already present in source

- URL scheme guard.
- Safe relative path normalization.
- Safe output join under selected root.
- Strict archive member validation.
- Atomic settings/cache writes.
- Corrupt settings backup handling.
- Rotating file logging.
- Duplicate log handler prevention.
- Sensitive log redaction.
- Non-blocking GUI log queue.
- Batched GUI log flush.
- Download lock around legacy `os.chdir()` paths.
- Failed asset report fallback.
- Failed URL report for batch mode.
- Optional dependency handling for optional features.

---

## 5. Bug/risk notes

### Old scripts using removed Website flags

- Location: CLI argument parser.
- Impact: old automation using `--website`, `-W`, or `--website-folder` exits with argparse error.
- Cause: intentional v1.0 cleanup.
- Solution: migrate to `--icc` and `--icc-folder`.
- Regression risk: only affects old scripts, not old batch/settings internal keys.
- Verification: tests confirm old flags are not accepted and new ICC flags are accepted.

### Optional dependency absence

- Location: optional modules such as `json5`, `httpx`, `tldextract`, `cloudscraper`, `selenium`, `yt-dlp`, `gallery-dl`, `keyring`, pandas/openpyxl/xlrd.
- Impact: optional features may be unavailable.
- Cause: optional feature design.
- Solution: document dependency groups and provide `--dependency-check`.
- Regression risk: low for core downloads.
- Verification: dependency check and docs.

### Live site variability

- Location: remote websites and third-party viewers.
- Impact: missing assets or blocked downloads.
- Cause: changing site structure, rate limits, Cloudflare, dynamic rendering.
- Solution: ICC Folder, deep scan, lower threads, Cloudflare modes, gallery-dl, Playwright/Selenium, AI fallback, reports.
- Regression risk: external, site-dependent.
- Verification: manual testing on representative URLs.

---

## 6. Acceptance checklist

- [x] Source compiles with `py_compile`.
- [x] CLI help works.
- [x] `--icc` accepted.
- [x] `--icc-folder` accepted.
- [x] old Website CLI flags intentionally rejected.
- [x] Tests pass.
- [x] README expanded with full feature overview.
- [x] Advanced features documented.
- [x] MIT License included.
- [x] Credits included.
- [x] Light/dark logo assets included.
- [x] GitHub release notes included.

---

## 7. Recommended next development

- Add a dedicated `--diagnose` command that exports one consolidated diagnostic report.
- Add GUI Help → Diagnostics dialog.
- Add GUI Help → Open Log Folder.
- Add GUI Help → Reset Settings with backup.
- Add Windows EXE smoke-test script.
- Add integration tests using local mock HTTP server.
- Add sample fixtures for ICC, ICC Plus, CYOAP Vue, and Pure Website modes.
