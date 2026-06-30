---
name: Bug report
about: Report a download failure, crash, or incorrect behavior
title: "[Bug] "
labels: bug
---

## Summary

A clear, one-line description of the problem.

## Source URL and mode

- **CYOA URL:** (the page you tried to download)
- **Output mode:** (e.g. ICC Folder, ICC ZIP, Embedded JSON, Pure Website, CYOAP Vue)
- **Interface:** GUI or CLI (paste the exact command if CLI)

## What happened

What actually happened, including any error message shown in the log or terminal.

## What you expected

What you expected to happen instead.

## Environment

- **OS:** (Windows / macOS / Linux + version)
- **How you run it:** Windows EXE, or Python (paste `python --version`)
- **App version:** (shown in the title bar / `--help`, e.g. 1.0.2)
- **Cloudflare / proxy / DNS / AI Assist enabled?** (yes/no, which)

## Logs and reports

Please attach or paste relevant lines from:

- `cyoa_downloader.log`
- `backup_report.txt`
- `failed_assets.txt` / `failed_images.txt`

Remove any private URLs, cookies, API keys, or tokens before posting.

## Verification (optional but helpful)

Output of:

```
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
```
