# CYOA Downloader — Rust preview

This is a preview of the Rust rewrite of CYOA Downloader. The Python app is still the reference. Anything not listed under "Supported" still needs the Python app.

Files:
- `cyoa-downloader.exe`: the command-line tool.
- `cyoa-desktop.exe`: the desktop window.
- `LICENSES/` and `THIRD_PARTY_NOTICES.md`: licences of the bundled libraries.

Both programs read and write the same settings file as the Python app: `%USERPROFILE%\.cyoa_downloader\settings.json`. They share the image cache too.

## Supported

- **Output modes:** embedded JSON (images inlined), ZIP with external images, or both. For the same project the files match the Python output byte for byte (timestamps excluded).
- **Reports:**
  - `<name>_metadata.json`
  - `failed_images.txt`
  - `failed_assets.txt`, or the existing `backup_report.txt` gets the failures appended.
  - `cyoa_downloader.log`: rotated at 5 MB; tokens and passwords are masked.
- **Batch lists:** `.txt` files with lines of the form `url | filename | mode`, or `.csv` files. Failed URLs go to `failed_urls.txt`.
- **Network:**
  - Proxy: from the environment, manual, or off; per-scheme proxies and bypass hosts.
  - Custom DNS: UDP, TCP, DNS-over-TLS, DNS-over-HTTPS, BebasDNS.
  - VPN guard: refuses to download unless a VPN interface is up.
  - Network flags given on the command line are saved to `settings.json`, as in the Python app.
- **CYOA Manager:** with `--cyoa-manager` or the desktop checkbox, finished downloads are added to the CYOA Manager library. ZIP downloads are extracted beside the archive first.

- **Website modes:** ICC ZIP/folder, Pure Website, CYOAP Vue, with Classic, Smart, Browser (Chrome/Edge/Brave over CDP) and Auto archive strategies; deep scan, route crawl, runtime capture, automatic recovery of failed assets, Retry Assets.
- **Offline viewers:** packaging with registered viewers (ICC Plus, ICC Remix, Viewer 1.8, Lt. Ouroumov and others), the cheat panel, `--modernize-library`, the local preview server (`--serve`).
- **Fallbacks:** yt-dlp YouTube audio, gallery-dl, Cloudflare (cloudscraper-style and FlareSolverr), browser transport, Discord attachment refresh.
- **Integrations:** itch.io (itch-dl, HTML5 offline preparation), AI Assist (Anthropic, OpenAI, Gemini, Ollama, OpenAI-compatible custom endpoints), external plugins (`docs/PLUGINS.md`), XLSX/XLS/CSV/TXT batch import.
- **Security:** the browser runs behind a local relay that blocks internal addresses and keeps proxy passwords out of the browser; on Windows the system TLS stack is used.

- **Browser cookies:** cookies from installed Chrome/Edge/Brave/Chromium/Firefox profiles for authenticated assets and YouTube audio (read through `yt-dlp --cookies-from-browser`; set `CYOA_DISABLE_BROWSER_COOKIES=1` to turn it off). A browser that is open can lock its cookie database, and newer Chrome/Edge app-bound encryption can refuse the export; close the browser or supply an exported cookies.txt.
- **Retry Assets** also continues interrupted or failed route archives.
- **Bundled viewers:** viewer archives next to the program (including `ICCPlus-main.zip`) are registered at startup.

## Differences from the Python app

`docs/FEATURE_PARITY.md` lists every known difference. Notable ones: the cloudscraper JavaScript-challenge solver is not included (both apps use FlareSolverr for current Cloudflare challenges); resumed route archives use the route crawler without a new browser runtime capture.

`docs/FEATURE_PARITY.md` in the source tree has the full list, with every known difference from the Python app.

## Command line

```
cyoa-downloader <url> [filename] [-o DIR] [-z | -b] [-t N] [-w SECONDS]
cyoa-downloader -L list.txt -o DIR
cyoa-downloader <url> --cyoa-manager
cyoa-downloader <url> --proxy http://127.0.0.1:7890
cyoa-downloader <url> --dns 1.1.1.1 --dns-protocol dot
cyoa-downloader <url> --vpn-policy require
```

`cyoa-downloader --help` lists every option.

**Exit codes:**
- 0: success.
- 1: at least one download failed.
- 2: invalid arguments or network configuration.
- 130: cancelled with Ctrl-C.

## Known limitations

- Live VPN-interface detection on Windows, DNS-over-TLS against a real server, and real proxies have only been covered by automated tests, not by manual runs.
- In the desktop app, only the network settings and the CYOA Manager toggle are shared with `settings.json`. The output folder, mode and worker count are stored in the window's own state, as before.
