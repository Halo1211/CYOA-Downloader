# CLI Reference

## Main modes

| Flag | Internal destination | Output |
| --- | --- | --- |
| No mode flag | `embed` | Embedded JSON. |
| `--zip` | `zip` | ZIP with project JSON and external assets. |
| `--both` | `both` | Embedded JSON and ZIP. |
| `--icc` | `website_zip` | ICC offline viewer ZIP. |
| `--icc-folder` | `website_folder` | ICC offline viewer folder. |
| `--pure-website` | `pure_website_zip` | Custom-site ZIP without project JSON discovery. |
| `--pure-website-folder` | `pure_website_folder` | Custom-site folder without project JSON discovery. |

## Removed legacy flags

The following flags were intentionally removed in v1.0 Release:

| Removed flag | Replacement |
| --- | --- |
| `--website` | `--icc` |
| `-W` | `--icc` |
| `--website-folder` | `--icc-folder` |

## Verified mapping

| CLI input | Expected destination | Status |
| --- | --- | --- |
| `--icc` | `website_zip` | Supported. |
| `--icc-folder` | `website_folder` | Supported. |
| `--website` | None | Removed; argparse exits with code 2. |
| `-W` | None | Removed; argparse exits with code 2. |
| `--website-folder` | None | Removed; argparse exits with code 2. |

Internal keys `website_zip` and `website_folder` remain supported for batch/settings/manifest compatibility.
