<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img alt="CYOA Downloader logo" src="assets/logo-light.png" width="170">
  </picture>
</p>

<h1 align="center">CYOA Downloader</h1>

<p align="center">
  An ICC/CYOA backup utility with a GUI, CLI, JavaScript-aware website archiving,
  batch queues, media recovery, advanced network routing, local previews, and
  output verification.
</p>

<p align="center">
  <a href="https://github.com/Halo1211/CYOA-Downloader/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Halo1211/CYOA-Downloader/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Halo1211/CYOA-Downloader/releases/latest"><img alt="Version" src="https://img.shields.io/badge/version-v1.0.8-20c997.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3aa6d0.svg">
  <img alt="UI" src="https://img.shields.io/badge/UI-PySide6-d633b8.svg">
  <a href="https://github.com/mikf/gallery-dl"><img alt="Powered by gallery-dl" src="https://img.shields.io/badge/powered%20by-gallery--dl-263238.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-green.svg"></a>
</p>

> **Important**  CYOA Downloader is an independent community utility. Download
> only content you are permitted to access and retain. It is not affiliated with
> CYOA.CAFE, gallery-dl, or any website handled by the downloader.

---

## What it does

CYOA Downloader saves interactive web projects for offline inspection. It can
preserve an ICC-style project, archive a normal or JavaScript-driven website,
recover images and media, package the result as a folder or ZIP, and verify
that local references are complete.

The downloader supports four website archive strategies:

- **Classic** preserves the historical single-page workflow.
- **Smart** adds a bounded same-origin story-route crawl.
- **Browser** adds runtime capture for assets exposed only after JavaScript runs.
- **Auto** profiles the target and selects the lightest complete strategy.

Auto mode recognizes ICC project data, CYOA.CAFE records, route trees, and
common runtime frameworks. Login, telemetry, comments, payments, mutations,
and unrelated external domains are not treated as story routes.

See the [JavaScript Archive Guide](docs/JAVASCRIPT_ARCHIVE_GUIDE.md) and
[Auto Safe Archive notes](docs/AUTO_SAFE_ARCHIVE.md) for the complete behavior.

## Start quickly

### Windows executable

Download `CYOA-Downloader-Windows-x64.zip` from the
[GitHub Releases page](https://github.com/Halo1211/CYOA-Downloader/releases),
extract it, and run `CYOA Downloader.exe`. The executable is unsigned, so
Windows SmartScreen may require confirmation.

Large or machine-specific helpers are detected separately instead of being
silently bundled:

- FFmpeg for media conversion and merging;
- Deno plus `yt-dlp-ejs` for current YouTube extraction;
- Chrome/Chromium and a driver for Selenium fallback;
- Playwright Chromium for browser automation;
- unrar or 7-Zip for RAR extraction.

### Run from source

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py
```

Optional recovery and batch packages can be installed with:

```powershell
pip install -r requirements-optional.txt
python -m playwright install chromium
```

## GUI workflow

Run `python cyoa_downloader.py` without arguments to open the GUI. For a
JavaScript-heavy project:

1. Choose **Pure Website Folder**.
2. Open **Settings**.
3. Set **JavaScript Archive Policy** to **Auto**.
4. Configure the page and depth safety caps.
5. Add the URL to the queue and select **Download All**.
6. Open the result with **Serve**; modern JavaScript applications should not
   rely on direct `file://` access.

Large route-based stories can start with 800 pages and a depth of 30. These are
safety caps, not targets: discovery stops when no new routes or assets appear.

Each queued URL keeps its own filename and output mode. Select a row's mode
badge to change it without deleting and re-adding the URL. **Export List…**
writes the queue to CSV or TXT for later import. See the
[GUI Queue Guide](docs/GUI_QUEUE_GUIDE.md).

## Advanced network settings

Open **Settings → Network** to configure:

- environment, disabled, or manual proxy mode;
- common and per-scheme HTTP/HTTPS proxies;
- HTTP, HTTPS, SOCKS4, SOCKS5, and `socks5h` routing;
- proxy bypass/`NO_PROXY` hosts;
- system DNS, UDP, TCP, DNS-over-HTTPS, or DNS-over-TLS;
- Cloudflare `1.1.1.1`, Google, Quad9, BebasDNS, DoH, and DoT presets;
- DNS timeout, IPv6, system fallback, and custom ports;
- a fail-closed, application-level VPN interface guard.

The compact Proxy and DNS controls in the main GUI remain synchronized with
the advanced profile. **Validate offline** checks the configuration and local
network interfaces without requesting a website or public test URL.

The VPN guard does not create, start, or reconfigure a VPN tunnel. `system`
uses the operating system route table. `require` blocks downloader traffic when
the requested VPN-like interface is not active. For proxy-side hostname
resolution, prefer `socks5h://`.

Example DoH, SOCKS, and required VPN configuration:

```powershell
python cyoa_downloader.py URL `
  --dns-protocol doh `
  --dns https://cloudflare-dns.com/dns-query `
  --no-dns-fallback-system `
  --proxy-mode manual `
  --proxy socks5h://127.0.0.1:1080 `
  --vpn-policy require `
  --vpn-interface WireGuard
```

See the [Network, DNS, Proxy, and VPN Guard Guide](docs/NETWORK_GUIDE.md) for
the transport matrix, privacy trade-offs, and troubleshooting steps.

## Discord attachment recovery

Discord attachment recovery is integrated into the normal download pipeline.
When an attachment CDN URL has expired, the downloader can use Discord API v10
to refresh it and then replace the project reference with the downloaded local
asset.

Set a bot token for one process with:

```powershell
$env:DISCORD_BOT_TOKEN = "YOUR_BOT_TOKEN"
python cyoa_downloader.py "https://example.com/cyoa/" -o "output"
```

The GUI field is available under **Settings / Maintenance → Discord Bot
Token**. Use a bot token from the Discord Developer Portal—never a password,
user token, or personal account token. Existing attachment URLs do not require
reading channels or messages. See the
[Discord Attachment Recovery Guide](docs/DISCORD_ATTACHMENTS_GUIDE.md).

## Settings and safety

Active settings are stored in `~/.cyoa_downloader/settings.json`. Invalid
manually edited values are normalized to safe ranges. Portable settings export
removes secret fields automatically, but the active settings file can still
contain plain credentials and must not be shared.

Safe browser interaction permits a narrow allowlist such as **Load More** or
**Show More**. Form submission, login, voting, comments, reports, payments,
popups, and mutation requests remain blocked.

Cloudflare handling uses a normal request first and activates a configured
fallback only when a challenge is detected. Example:

```powershell
python cyoa_downloader.py URL --cloudflare auto --cloudflare-priority flaresolverr-first
```

## CLI examples

```powershell
python cyoa_downloader.py --help
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py "https://example.com/story/" `
  --pure-website-folder `
  --archive-strategy auto `
  --archive-max-pages 800 `
  --archive-max-depth 30 `
  --output "output"
python cyoa_downloader.py --verify "output"
```

## Diagnostics and media helpers

The Diagnostics panel checks Python packages, command-line tools, browser
backends, write permissions, settings, cache state, and frozen PyInstaller
resources. A missing optional helper only disables the related feature.

Current YouTube extraction normally requires an up-to-date `yt-dlp`, the
`yt-dlp-ejs` package, and a JavaScript runtime such as Deno. A cookie file does
not replace those components. Never commit cookies, settings, API keys, bot
tokens, or downloaded content.

## Build the Windows package

```powershell
.\tools\build_windows.ps1
```

If PowerShell policy blocks scripts:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\build_windows.ps1
```

The build creates `dist\CYOA-Downloader-Windows-x64.zip`. FFmpeg, Deno,
browsers, and RAR helpers remain diagnosed external dependencies.

## Repository map

| Path | Purpose |
| --- | --- |
| `cyoa_downloader.py` | CLI and GUI entry point |
| `cyoa_downloader_app/` | Application packages |
| `docs/` | User, troubleshooting, and maintainer guides |
| `examples/` | Safe sample batch inputs and templates |
| `tests/` | Offline regression tests |
| `tools/` | Verification and Windows build helpers |
| `.github/` | CI, issue templates, and release automation |

## Development and verification

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall -q cyoa_downloader_app cyoa_downloader.py
python -m pytest -q
ruff check cyoa_downloader.py cyoa_downloader_app
```

The current offline regression suite contains 438 passing tests with 7 optional
tests skipped when their runtime conditions are unavailable.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Security
reports belong in [SECURITY.md](SECURITY.md). This project is distributed under
the [MIT License](LICENSE).
