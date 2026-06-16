<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="160">
  </picture>
</p>

# CYOA Downloader — v1.0 Release

CYOA Downloader is a Python utility for backing up CYOA projects into local, portable output formats. It supports embedded JSON, ZIP packages, offline ICC viewer archives, local ICC folders, batch imports, asset scanning, font detection, audio/video detection, and localhost preview tools.

This release focuses on stabilization, GitHub readiness, consistent ICC naming, and a light/dark compatible project logo.

## Main features

- Tkinter/customtkinter GUI that opens when the script is run without arguments.
- CLI mode for automation and repeatable backups.
- Embedded JSON output with base64 images.
- ZIP output with project JSON and external assets.
- ICC ZIP and ICC Folder modes for offline viewer backup.
- Dedicated CYOAP Vue flow for `dist/platform.json` and `dist/nodes/list.json` projects.
- Deep asset scanning for images, fonts, audio, video, CSS, JavaScript, and viewer files.
- Failed asset reports, backup reports, rotating logs, and batch failure logs.
- Local Serve preview with optional debugging/accessibility/QoL helpers for localhost output only.
- Backward-compatible internal mode keys for old batch/settings/manifest files.

## Install

### Windows

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

## Run the GUI

```bash
python cyoa_downloader.py
```

The GUI uses the bundled logo assets from `assets/logo-light.png` and `assets/logo-dark.png`. If those files are unavailable, the script falls back to embedded logo data and then to a text fallback.

## Run the CLI

```bash
python cyoa_downloader.py "https://example.com/cyoa" -o output
python cyoa_downloader.py --zip "https://example.com/cyoa" -o output
python cyoa_downloader.py --both "https://example.com/cyoa" -o output
```

## ICC Mode

Legacy user-facing “Website Mode” terminology has been replaced with **ICC Mode**.

```bash
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output.zip
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder
```

### Legacy CLI flags

The old CLI flags `--website`, `-W`, and `--website-folder` were intentionally removed in v1.0 Release for naming consistency. Use `--icc` and `--icc-folder` instead.

### Backward compatibility

Internal mode keys are intentionally unchanged:

- `website_zip`
- `website_folder`

These keys may still appear in existing CSV/XLSX batch files, settings files, and manifests. They remain supported to avoid breaking older user data. New batch files may use either `website_zip` / `website_folder` or the friendly aliases `icc` / `icc_zip` / `icc_folder`.

## Batch import

TXT:

```text
https://example.com/cyoa/
https://example.com/cyoa2/ | MyFilename
https://example.com/cyoa3/ | MyFilename | website_zip
https://example.com/cyoa4/ | MyFolder | icc_folder
```

CSV/XLSX columns are case-insensitive:

| Column | Required | Notes |
| --- | --- | --- |
| `url` / `link` | Yes | Full URL beginning with `http://` or `https://`. |
| `filename` / `name` / `output` / `title` | No | Output filename or folder name. |
| `mode` / `output_mode` / `type` | No | `embed`, `zip`, `both`, `website_zip`, `website_folder`, `icc`, `icc_zip`, `icc_folder`, `cyoap_vue_zip`, `cyoap_vue_folder`, or `auto`. |

## Disclaimer

Use this tool only for content you are permitted to archive. Local Serve tools and userscript helpers are intended for localhost/offline debugging, accessibility checks, and quality-of-life testing of downloaded CYOA output. They are not intended for attacking live websites or bypassing third-party terms.

## Credits

This project includes a bundled localhost helper inspired by IntCyoaEnhancer:

- Name: IntCyoaEnhancer
- Author: agreg
- License: MIT
- Source: GreasyFork script 438947

The bundled helper is a localhost/offline integration route. It does not claim ownership of the original IntCyoaEnhancer project.

See `docs/CREDITS.md` for the full credits text.

## License

This repository is released under the MIT License. See `LICENSE`.

## Contributing

Small, backward-compatible patches are welcome. Please keep internal mode keys stable, avoid breaking existing batch/settings/manifest files, and include smoke tests for CLI argument changes, path safety, and batch import behavior.
