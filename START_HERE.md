# Start here

## Windows users

1. Download `CYOA-Downloader-Windows-x64.zip` from the [GitHub Releases page](https://github.com/Halo1211/CYOA-Downloader/releases).
2. Extract the ZIP.
3. Run `CYOA Downloader.exe`.
4. Open **Diagnostics** and fix any `FAIL` items.
5. Paste a project URL, choose **ICC Folder**, and click **Download All**.

The first diagnostic run may show optional warnings. They matter only for the
feature named by the warning. For YouTube audio, pay attention to **yt-dlp**,
**yt-dlp-ejs**, **YouTube JavaScript runtime**, and **FFmpeg**.

## Python users

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py
```

Install optional integrations only when needed:

```powershell
pip install -r requirements-optional.txt
python -m playwright install chromium
```

## If a download is incomplete

Open `backup_report.txt`, then use **Verify**, **Retry Assets**, **Retry
Images**, or **Retry Audio**. Run `--self-test` for an offline health check and
read [Troubleshooting](docs/TROUBLESHOOTING.md) for platform-specific fixes.
