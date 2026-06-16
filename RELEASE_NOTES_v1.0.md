# Release Notes — CYOA Downloader v1.0 Release

## Highlights

- v1.0 Release branding.
- MIT License prepared for GitHub publication.
- New light/dark compatible project logo in program and repository assets.
- User-facing Website terminology replaced with ICC terminology.
- New CLI flags: `--icc` and `--icc-folder`.
- Removed legacy CLI flags: `--website`, `-W`, and `--website-folder`.
- Internal compatibility preserved for `website_zip` and `website_folder` in batch/settings/manifest data.
- Added GitHub-ready documentation and test scaffolding.

## Migration

Replace old commands:

```bash
python cyoa_downloader.py --website "URL" -o output.zip
python cyoa_downloader.py -W "URL" -o output.zip
python cyoa_downloader.py --website-folder "URL" -o output_folder
```

with:

```bash
python cyoa_downloader.py --icc "URL" -o output.zip
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```
