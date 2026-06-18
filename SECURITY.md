# Security Policy

CYOA Downloader processes remote URLs, archives, project JSON, HTML, CSS, JavaScript, and user-selected local files. Security work is therefore part of normal maintenance, not an afterthought.

## Supported version

| Version | Supported |
| --- | --- |
| 1.0.1 | Yes |

## Security boundaries

The application attempts to reduce risk in these areas:

- URL scheme validation for download sources.
- Safer path joining for URL-derived output paths.
- Strict archive member validation to reduce ZIP slip risk.
- Archive extraction size/count limits to reduce decompression abuse risk.
- Atomic settings/cache writes.
- Log redaction for token-like and secret-like values.
- Non-fatal handling for missing optional dependencies.
- Optional AI key storage modes that avoid plain settings files by default.

The application does **not** guarantee that arbitrary remote websites are safe. A downloaded offline viewer may still contain JavaScript from the original site. Open unknown offline outputs with the same care you would use for any downloaded web content.

## Responsible disclosure

Please report security issues privately if possible. Include:

- affected version;
- operating system;
- exact command or GUI workflow;
- minimal reproduction steps;
- whether the issue involves path traversal, archive extraction, token leakage, remote code execution, unsafe URL handling, or dependency behavior;
- a harmless proof of concept when possible.

Do not include real API keys, cookies, private URLs, or personal data in the report.

## Sensitive data guidance

- Do not paste API keys into public issues.
- Prefer `session`, `env`, or `keyring` AI key storage.
- Avoid `plain` storage on shared machines.
- Review `cyoa_downloader.log`, `backup_report.txt`, and issue attachments before uploading them.
- The logger redacts common secret patterns, but users should still manually inspect logs before sharing.

## Dependency security

Dependencies are not installed automatically. Users decide what to install. Optional tools such as FFMPEG, yt-dlp, Playwright, Selenium, gallery-dl, cloudscraper, and FlareSolverr should be installed from trusted sources and kept updated.
