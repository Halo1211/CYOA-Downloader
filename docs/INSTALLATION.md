# Installation Guide — CYOA Downloader v1.0 Release

This page is the beginner-friendly installation guide. It is intentionally more detailed than the quick commands in the README.

## Choose your installation method

| Method | Recommended for | What you run |
| --- | --- | --- |
| **A. Windows EXE** | Normal Windows users after an EXE release is published | `CYOA Downloader.exe` |
| **B. Python source install** | Current release, developers, and users who want all features | `python cyoa_downloader.py` |
| **C. Development install** | Contributors and maintainers | `pytest`, editable source workflow |

At the moment, the source install is the main supported method. The EXE build guide is included for packaging later.

---

## Method A — Windows EXE install, when the EXE release is available

Use this method only after a Windows release asset exists in GitHub Releases.

1. Open the GitHub repository.
2. Go to **Releases**.
3. Download the Windows package, for example:

```text
CYOA-Downloader-v1.0-Windows-x64.zip
```

4. Extract the ZIP file.
5. Open the extracted folder.
6. Run:

```text
CYOA Downloader.exe
```

Recommended EXE folder layout:

```text
CYOA-Downloader-v1.0-Windows-x64/
├─ CYOA Downloader.exe
├─ README_FIRST.txt
├─ LICENSE
└─ assets/
```

Do not run the EXE directly from inside the ZIP preview window. Extract it first.

If Windows SmartScreen appears, choose **More info → Run anyway** only if you downloaded the file from the official release page.

---

## Method B — Python source install, recommended now

### Step 1 — Install Python

Install **Python 3.10 or newer**.

Windows users should install Python from the official Python installer and enable:

```text
Add python.exe to PATH
```

Check Python after installation:

```powershell
python --version
```

or:

```powershell
py --version
```

Expected result:

```text
Python 3.10.x or newer
```

### Step 2 — Download the project

You can use either Git or GitHub ZIP download.

#### Option 1 — Download from GitHub as ZIP

1. Open the repository page.
2. Click **Code**.
3. Click **Download ZIP**.
4. Extract the ZIP.
5. Open the extracted project folder.

The folder should contain files like this:

```text
CYOA-Downloader/
├─ cyoa_downloader.py
├─ requirements.txt
├─ README.md
├─ LICENSE
├─ assets/
└─ docs/
```

#### Option 2 — Clone with Git

```bash
git clone https://github.com/Halo1211/CYOA-Downloader.git
cd CYOA-Downloader
```

### Step 3 — Open a terminal in the project folder

On Windows:

1. Open the `CYOA-Downloader` folder.
2. Click the address bar.
3. Type `powershell`.
4. Press Enter.

Or right-click inside the folder and choose **Open in Terminal**.

### Step 4 — Create a virtual environment

A virtual environment keeps this project’s packages separate from your global Python installation.

#### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If `py` is not available:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell blocks activation, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

#### Windows CMD

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
```

#### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### Step 5 — Install dependencies

Recommended full install:

```bash
pip install -r requirements.txt
```

This installs the normal GUI/CLI dependencies plus optional helpers used by advanced features such as deep scan, Cloudflare recovery, browser fallback, media extraction, batch Excel import, and AI key storage.

If you only want the minimum CLI downloader, install the core dependencies manually:

```bash
pip install requests urllib3 beautifulsoup4
```

For the graphical interface, also install:

```bash
pip install customtkinter Pillow
```

For Excel batch files, also install:

```bash
pip install pandas openpyxl xlrd
```

For advanced recovery features, install:

```bash
pip install json5 tldextract httpx[h2] cloudscraper selenium yt-dlp gallery-dl keyring dnspython browser-cookie3
```

For Playwright browser fallback, install the package and browser runtime:

```bash
pip install playwright
python -m playwright install chromium
```

### Step 6 — Verify the installation

Run these commands from inside the project folder:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

Expected behavior:

- `--dependency-check` lists installed and missing modules.
- `--self-test` runs offline smoke checks.
- `--help` shows the command-line options.
- The active ICC flags are `--icc` and `--icc-folder`.
- Old Website CLI flags are intentionally removed in v1.0 Release.

### Step 7 — Start the GUI

```bash
python cyoa_downloader.py
```

or:

```bash
python cyoa_downloader.py --gui
```

### Step 8 — Run a first CLI download

ICC ZIP output:

```bash
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output
```

ICC folder output:

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder
```

ICC folder output with local preview server:

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --serve
```

Replace `https://example.com/cyoa` with the actual ICC/CYOA URL.

---

## Optional feature setup

### Cloudflare recovery

Basic Cloudflare mode using cloudscraper:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare cloudscraper
```

Automatic recovery mode:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare auto
```

### FlareSolverr

FlareSolverr is not a Python package. It is a separate local service.

After starting FlareSolverr, test it:

```bash
python cyoa_downloader.py --flaresolverr-test --flaresolverr-url http://localhost:8191/v1
```

Use it for a download:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --cloudflare flaresolverr \
  --flaresolverr-url http://localhost:8191/v1
```

### Proxy / DNS / BebasDNS

Manual proxy:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --proxy http://127.0.0.1:7890 \
  --proxy-mode manual
```

Disable environment proxy:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --proxy-mode disabled
```

Use BebasDNS preset:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --bebasdns default
```

### HTTP/2 deep scan

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --http2
```

Disable HTTP/2:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-http2
```

### AI Assist

AI Assist is optional. The downloader can run without it.

Run-only OpenAI key:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider openai \
  --ai-mode auto_fallback \
  --ai-key "YOUR_KEY"
```

Local Ollama:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider ollama \
  --ai-model llama3.1 \
  --ai-mode diagnostics
```

Clear a stored AI key:

```bash
python cyoa_downloader.py --ai-clear-key
```

---

## Common installation problems

### `python` is not recognized on Windows

Use:

```powershell
py --version
```

If `py` works, replace `python` with `py` in the commands. If neither works, reinstall Python and enable **Add python.exe to PATH**.

### PowerShell cannot activate `.venv`

Run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Tkinter is missing on Linux

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install python3-tk
```

Fedora:

```bash
sudo dnf install python3-tkinter
```

Arch:

```bash
sudo pacman -S tk
```

### `pip install -r requirements.txt` is slow

Install core first:

```bash
pip install requests urllib3 beautifulsoup4 customtkinter Pillow
```

Then add optional packages only when needed.

### Playwright is installed but browser fallback does not work

Run:

```bash
python -m playwright install chromium
```

### Antivirus warns about future EXE builds

This can happen with PyInstaller applications. Prefer downloading the EXE ZIP from the official GitHub release page. The source version can always be run directly with Python.

---

## Method C — Development install

Use this if you want to modify the code or run tests.

```bash
git clone https://github.com/Halo1211/CYOA-Downloader.git
cd CYOA-Downloader
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate
```

Install dependencies and test tools:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
python -m pytest -q
```

---

## After installation: recommended first checks

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --userscript-info
```

Then launch the GUI:

```bash
python cyoa_downloader.py
```

For packaging a Windows EXE, see [`EXE_BUILD.md`](EXE_BUILD.md).
