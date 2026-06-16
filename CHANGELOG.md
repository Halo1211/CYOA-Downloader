# Changelog

## v1.0 Release

### Added

- MIT License.
- Dark/light compatible logo assets.
- GitHub-ready repository structure.
- Expanded README with full feature overview.
- Complete documentation pages:
  - `docs/FEATURES.md`
  - `docs/CLI.md`
  - `docs/GUI.md`
  - `docs/HOW_IT_WORKS.md`
  - `docs/INSTALLATION.md`
  - `docs/USAGE.md`
  - `docs/TROUBLESHOOTING.md`
  - `docs/EXE_BUILD.md`
  - `docs/CREDITS.md`
- Tests for CLI aliasing, path handling, and batch import.
- Formal release notes.

### Changed

- Version branding changed to `v1.0 Release`.
- User-facing Website Mode terminology changed to ICC Mode.
- CLI now uses:
  - `--icc`
  - `--icc-folder`
- README and docs now document advanced features including:
  - AI Assist;
  - deep scan;
  - Cloudflare/cloudscraper/FlareSolverr;
  - proxy/DNS/BebasDNS/HTTP2;
  - gallery-dl;
  - yt-dlp;
  - Playwright/Selenium headless fallback;
  - CYOAP Vue;
  - Pure Website modes;
  - local preview and userscript helper;
  - CYOA Manager;
  - itch.io support;
  - diagnostics and maintenance commands.

### Removed

- Old Website CLI flags were intentionally removed for consistency:
  - `--website`
  - `-W`
  - `--website-folder`

### Preserved compatibility

- Internal mode key `website_zip` remains supported.
- Internal mode key `website_folder` remains supported.
- Old batch/settings/manifest data using those internal keys remains compatible.

### Security/stability notes

- Safe output path handling documented.
- Strict archive path validation documented.
- Atomic settings/cache writes documented.
- Sensitive log redaction documented.
- Failed asset and failed URL reporting documented.
