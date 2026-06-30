# Start here

New to CYOA Downloader? This page takes you from zero to a saved CYOA in a few minutes. It is all you need to read first.

## What this tool does

It **saves an interactive CYOA to your computer** so you can read and play it offline, even if the original website disappears. You provide a link; it downloads everything and gives you a folder you can keep.

## Choose how to run it

### Windows, no setup

1. Open the **[Releases page](../../releases)**.
2. Download the **`-Windows-x64.zip`** asset.
3. **Right-click → Extract All.**
4. Double-click **`CYOA Downloader.exe`**.

> If Windows shows *“Windows protected your PC,”* choose **More info → Run anyway**. The project is open-source and the executable is simply not code-signed.

### macOS, Linux, or Windows from source

1. Install **Python 3.10+** from [python.org/downloads](https://www.python.org/downloads/). On Windows, enable **“Add Python to PATH.”**
2. Download this project (green **`< > Code → Download ZIP`**) and extract it.
3. Open a terminal in the folder and run:

**Windows (PowerShell)**
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

You install once. Next time, activate `.venv` and run `python cyoa_downloader.py` again.

## Save your first CYOA

1. **Paste the CYOA link** into the URL field.
2. Choose where to save it.
3. Select **ICC Folder** — a folder you can open and play offline (the best starting choice).
4. Click **Download All**.
5. When it finishes, open the folder.

If a few images failed, open `backup_report.txt` and use **Retry Images** in the application.

## If you get stuck

```bash
python cyoa_downloader.py --dependency-check
```

- **`python` not recognized** — reinstall Python with **Add to PATH** enabled.
- **PowerShell blocks the environment** — run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
- **No window / Tkinter error on Linux** — install Tk: `sudo apt install python3-tk`.
- Further help: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

---

For batch downloads, command-line usage, Cloudflare handling, and advanced options, see the **[README](README.md)** and the [full documentation](docs/GETTING_STARTED.md).
