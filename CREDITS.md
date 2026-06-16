# Credits

CYOA Downloader v1.0 Release is distributed under the MIT License.

## Bundled localhost helper credit

The project includes a bundled localhost/offline helper inspired by IntCyoaEnhancer.

- Name: IntCyoaEnhancer
- Author: agreg
- License: MIT
- Source: GreasyFork script 438947
- Source URL: https://greasyfork.org/en/scripts/438947-intcyoaenhancer

The bundled helper is provided only for localhost/offline preview, debugging, accessibility checks, and quality-of-life testing. This repository does not claim ownership of the original IntCyoaEnhancer project.

## Optional external tools and libraries

Depending on installed dependencies and enabled features, the downloader may interoperate with:

- requests / urllib3 for HTTP downloads and retry handling.
- BeautifulSoup / bs4 for HTML parsing.
- customtkinter and Pillow for GUI and image/logo rendering.
- pandas, openpyxl, and xlrd for batch spreadsheet import.
- json5 for relaxed JSON parsing.
- tldextract for domain parsing.
- httpx for optional HTTP/2 requests.
- cloudscraper and FlareSolverr for optional Cloudflare recovery.
- yt-dlp for optional YouTube/SoundCloud media recovery.
- gallery-dl for optional gallery/post fallback.
- selenium / playwright for optional browser-based fallback detection.
- keyring for optional safer AI API key storage.
- CYOA Manager integration for optional local library registration/import/export.

Each third-party project remains under its own license.

## Responsible-use note

CYOA Downloader is intended for content you are permitted to archive. Local Serve helpers and recovery tooling should be used responsibly and only where allowed.
