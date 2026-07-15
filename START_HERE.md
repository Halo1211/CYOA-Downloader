# Start here

New to CYOA Downloader? Follow this page from top to bottom. You only need to
learn one workflow before reading the advanced documentation.

## The short version

1. Install or launch the application.
2. Paste one CYOA URL into the GUI.
3. Choose **ICC Folder**.
4. Click **Download All**.
5. Open the output folder and read `backup_report.txt` if anything looks missing.

**ICC Folder** is the best first choice because it creates a normal folder that
you can inspect, serve locally, and retry. Use **ICC ZIP** later when you want
one file to store or share.

## Choose how to run it

### Windows executable

1. Open the [Releases page](../../releases).
2. Download the asset ending in `-Windows-x64.zip`.
3. Right-click the ZIP and choose **Extract All**.
4. Open the extracted folder and double-click `CYOA Downloader.exe`.

Windows may show a SmartScreen warning because the executable is not
code-signed. Verify that the file came from the project's release page, then
choose **More info → Run anyway** if you trust the download.

### Run from Python

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/),
download this repository, and open a terminal in the extracted folder.

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

The setup is normally done once. Later, activate `.venv` and run
`python cyoa_downloader.py` again.

## Your first GUI backup

1. Paste the page URL into the URL field.
2. Choose an output folder, or keep the default.
3. Select **ICC Folder**.
4. Click **Add URL**, then **Download All**.
5. Wait for the final status message.
6. Use **Open Folder** to inspect the result.

Do not start with AI, Cloudflare, browser fallback, or high thread counts.
Those are recovery tools for a specific problem, not prerequisites for a normal
download.

## Editing the queue

You can edit a queued filename directly. To change the output mode, click the
mode badge on that row (for example, `auto`) and choose a new mode. The URL
stays in the queue; you do not need to remove and add it again.

Use **Export List…** to save the current queue as CSV or TXT. The export keeps
the URL, filename, and mode, so it can be imported again on another machine.
See [GUI Queue Guide](docs/GUI_QUEUE_GUIDE.md) for examples.

## If something fails

Run the checks from the repository folder:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
```

Then read [Troubleshooting](docs/TROUBLESHOOTING.md). Common first fixes are:

- `python` is not recognized: reinstall Python and enable **Add Python to PATH**.
- PowerShell blocks activation: run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.
- Linux has no GUI: install `python3-tk`.
- A few assets failed: use the GUI retry buttons and inspect `backup_report.txt`.
- The viewer is blank when opened directly: use the GUI **Serve** button.

For normal GUI workflows, continue to [User Guide](docs/USER_GUIDE.md). For
installation details, read [Getting Started](docs/GETTING_STARTED.md).
