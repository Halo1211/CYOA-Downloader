# Changelog

## v1.0 Release — 2026-06-17

### Added

- Added v1.0 Release branding across the application and documentation.
- Added light/dark compatible project logo assets in `assets/logo-light.png` and `assets/logo-dark.png`.
- Added GUI logo loading with external asset preference, embedded fallback, and text fallback.
- Added ICC batch aliases: `icc`, `icc_zip`, and `icc_folder`.
- Added GitHub-ready repository files, documentation, MIT License, and test scaffolding.

### Changed

- Renamed user-facing “Website ZIP/Folder” labels to “ICC ZIP/Folder”.
- Renamed user-facing “Website Mode” section labels to “ICC Mode / MODE ICC”.
- Updated CLI help text to use formal ICC terminology.
- Updated guide and README text to explain that old internal keys remain unchanged for compatibility.
- Made BeautifulSoup import failure produce a clear install message instead of failing at import time.

### Removed

- Removed old CLI flags `--website`, `-W`, and `--website-folder` by request for consistent ICC CLI naming.

### Compatibility notes

- Internal mode keys remain unchanged: `website_zip` and `website_folder`.
- Old CSV/settings/manifest values using `website_zip` and `website_folder` are still supported.
- CLI scripts should migrate from `--website` to `--icc`, and from `--website-folder` to `--icc-folder`.
