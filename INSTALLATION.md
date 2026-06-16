# Installation

## Requirements

- Python 3.10 or newer is recommended.
- Internet access is required for downloading remote CYOA assets.
- GUI mode requires `customtkinter` and a working Tk installation.

## Windows

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

## Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

## Optional dependencies

| Dependency | Purpose |
| --- | --- |
| `pandas`, `openpyxl`, `xlrd` | CSV/XLSX/XLS batch imports. |
| `json5` | More tolerant JSON-like project parsing. |
| `tldextract` | Safer domain extraction and URL classification. |
| `httpx[h2]` | Optional HTTP/2 fetching. |
| `yt-dlp` | YouTube/audio fallback download. |
| `selenium`, `playwright` | Optional browser-based recovery paths. |
| `gallery-dl` | Optional media/gallery fallback. |
| `cloudscraper` | Optional Cloudflare recovery path. |
| `dnspython` | DNS/DoH override support. |
| `keyring` | Optional secure AI key storage. |
| `plyer` | Optional desktop notifications. |
| `rarfile` | Optional archive support. |

Missing optional dependencies should not stop basic CLI/GUI startup. Features that need a missing dependency should display a clear install hint.
