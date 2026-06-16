# Installation — CYOA Downloader v1.0 Release

This guide covers standard installation, optional dependencies, and Windows EXE packaging preparation.

---

## 1. Requirements

- Python 3.10 or newer recommended.
- Internet access for downloading remote assets.
- Tkinter for GUI mode. Tkinter is included with most Windows Python installers. Linux users may need an OS package.

---

## 2. Windows install

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If `py` is not available:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

---

## 3. Linux install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If Tkinter is missing:

```bash
# Debian/Ubuntu example
sudo apt install python3-tk
```

---

## 4. macOS install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If your Python build lacks Tkinter, install a Python distribution that includes it, such as the official python.org build or Homebrew Python with Tkinter support.

---

## 5. Required dependencies

The project requires these for core functionality:

| Package | Purpose |
| --- | --- |
| `requests` | HTTP downloads. |
| `urllib3` | Retry adapter support used through requests. |
| `beautifulsoup4` | HTML/ICC parsing. |

---

## 6. Optional dependencies

Install only what you need.

| Package/tool | Purpose |
| --- | --- |
| `pandas` | CSV/XLSX/XLS batch import convenience. |
| `openpyxl` | XLSX reading through pandas. |
| `xlrd` | Legacy XLS reading through pandas. |
| `json5` | Relaxed JSON parsing fallback. |
| `httpx[h2]` | Optional HTTP/2 deep-scan fetching. |
| `tldextract` | Better domain/root analysis. |
| `cloudscraper` | Optional Cloudflare recovery mode. |
| `selenium` | Optional headless browser fallback. |
| `yt-dlp` | Optional YouTube/SoundCloud audio extraction. |
| `gallery-dl` | Optional gallery/post extraction fallback. |
| `keyring` | Safer OS credential storage for AI keys. |
| `Pillow` | Optional image/logo handling in some environments. |
| FlareSolverr | External service for Cloudflare browser solving. |
| itch backend/tooling | Optional itch.io asset support. |

Install common optional recovery dependencies:

```bash
pip install json5 httpx[h2] tldextract cloudscraper selenium yt-dlp gallery-dl keyring pandas openpyxl xlrd
```

---

## 7. Verify installation

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

Expected result:

- dependency check prints available/missing required and optional modules;
- self-test passes;
- help text shows `--icc` and `--icc-folder` but not old `--website` flags.

---

## 8. FlareSolverr note

FlareSolverr is not a Python package installed by `requirements.txt`. It is a separate local service. After running it, test:

```bash
python cyoa_downloader.py --flaresolverr-test
```

Then use:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --cloudflare flaresolverr \
  --flaresolverr-url http://localhost:8191/v1
```

---

## 9. AI provider setup

### Cloud provider, run-only key

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider openai \
  --ai-mode auto_fallback \
  --ai-key "YOUR_KEY"
```

### Environment variable

Set provider-specific environment variables, then use:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider anthropic \
  --ai-key-storage env \
  --ai-mode diagnostics
```

### Local Ollama

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider ollama \
  --ai-model llama3.1 \
  --ai-mode diagnostics
```

---

## 10. Development install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
python -m pytest -q
```

---

## 11. Windows EXE preparation

For packaging instructions, see [`EXE_BUILD.md`](EXE_BUILD.md). The recommended public distribution is a ZIP folder containing the EXE, assets, README, and license rather than a single loose EXE.
