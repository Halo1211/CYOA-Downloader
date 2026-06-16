# Changelog

## v1.0 Release

### Added

- GitHub-ready release structure.
- MIT License.
- Light/dark compatible project logo assets.
- Expanded GitHub documentation:
  - Full README.
  - Feature inventory.
  - Complete CLI reference.
  - GUI guide.
  - How-it-works architecture guide.
  - Installation guide.
  - Usage guide.
  - Troubleshooting guide.
  - Windows EXE build guide.
  - Credits and release notes.
- ICC CLI flags:
  - `--icc`
  - `--icc-folder`
- Batch aliases:
  - `icc`
  - `icc_zip`
  - `icc_folder`
- Test scaffold for CLI aliases, path safety, batch import, and help behavior.

### Changed

- User-facing Website Mode terminology replaced with ICC Mode.
- Version branding changed to `v1.0 Release`.
- README now documents feature matrix, CLI usage, mode behavior, reports, Serve tools, EXE build, and responsible-use policy.

### Removed

- Removed legacy CLI flags for naming consistency:
  - `--website`
  - `-W`
  - `--website-folder`

### Preserved compatibility

- Internal mode keys remain supported:
  - `website_zip`
  - `website_folder`
- Existing batch/settings/manifest data using internal keys remains compatible.

### Notes

- Users with old automation scripts must replace `--website`/`-W` with `--icc`, and `--website-folder` with `--icc-folder`.
