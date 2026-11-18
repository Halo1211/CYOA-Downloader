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

## Not yet supported (use the Python app)

- Website, ICC and folder modes (`--icc`, pure website, CYOAP Vue).
- Offline viewers and the local preview server.
- Fallbacks: gallery-dl, yt-dlp audio recovery, browser capture, Cloudflare/FlareSolverr.
- itch.io, Discord attachment refresh, AI features, plugins.
- XLSX batch import.

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
