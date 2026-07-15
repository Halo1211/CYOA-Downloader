# Frequently asked questions

## General

**What does this tool actually save?**
A complete, self-contained copy of an interactive CYOA: the project data, images, fonts, audio,
and (in ICC modes) the viewer itself, so you can open it offline later.

**Do I need to know how to code?**
No. Use the Windows executable or follow the copy-paste setup in
[GETTING_STARTED.md](./GETTING_STARTED.md). The desktop app is point-and-click.

**Is it free and open-source?**
Yes, under the MIT License. See [`LICENSE`](../LICENSE).

**Is this legal?**
The tool is intended for personal preservation, offline reading, accessibility, and local
debugging. Respect the original creators' rights and each site's terms. Do not use it to bypass
paid, private, or access-controlled content. See [`SECURITY.md`](../SECURITY.md).

## Choosing a mode

**Which output mode should I use?**
**ICC Folder** for the most complete offline playback, or **ICC ZIP** for a single shareable file.
The full comparison is in the [README](../README.md#output-modes) and
[USER_GUIDE.md](./USER_GUIDE.md).

**What is the difference between ICC Folder and Pure Website?**
ICC modes resolve the project data and build a proper offline viewer. Pure Website simply mirrors
the site's files without trying to resolve `project.json` first — use it for unusual or custom
sites where normal detection fails.

**How do I change a queued URL from Auto to another mode?**
Click the mode badge on the queue row and choose a new mode. The URL, filename,
and row position remain in place.

**How do I save or move my queue to another computer?**
Click **Export List…** and save a CSV or TXT file. It contains the URL, filename,
and mode. Use **Import List…** on the other computer.

## Common problems

**Some images are missing.**
First run `python cyoa_downloader.py --verify "your_output_folder"` to see exactly what is missing.
Then open `failed_images.txt`, use the in-app **Retry Images** button, and ensure deep scan is
enabled. For CDN-heavy sites, try `--http2`. See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md).

**I downloaded a Cloudflare "checking your browser" page instead of the CYOA.**
Set Cloudflare mode to `auto`, then `cloudscraper`, then run FlareSolverr if needed. Details in
[ADVANCED_FEATURES.md](./ADVANCED_FEATURES.md).

**The offline folder doesn't load when I double-click `index.html`.**
Open it through the local server instead (the **Serve** button, or `--serve`). Many viewers need
to be served over `http://` rather than `file://`.

**`python` is not recognized (Windows).**
Python is not on PATH. Reinstall Python and enable **Add Python to PATH**, then reopen the terminal.

**PowerShell won't let me activate the virtual environment.**
Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, confirm with `Y`, then retry.

**The window doesn't open on Linux.**
Install Tk: `sudo apt install python3-tk`.

**XLSX batch import fails.**
Install the optional dependencies with `pip install -r requirements-optional.txt`
from the repository root, or install `pandas` and `openpyxl` directly.

**Audio downloads fail.**
Install `yt-dlp` and FFmpeg, and confirm `ffmpeg -version` works.

## Backups and integrity

**How do I confirm a backup is complete?**
Run `python cyoa_downloader.py --verify "your_output_folder"`. To also detect corrupted or
truncated files later, capture a checksum baseline once with `--write-manifest`. See the
[verifying a backup](../README.md#verifying-a-backup) section.

**Does verifying change my files?**
No. `--verify` is read-only, and the checksum baseline is only written when you explicitly pass
`--write-manifest`.

## AI Assist

**Do I need an AI key to use the tool?**
No. AI Assist is entirely optional and off unless you configure it. It only helps locate hidden
project data on difficult custom sites.

**Can I use a local model?**
Yes. Use `--ai-provider ollama` with a local Ollama server; no hosted key is required.

## Contributing

**How can I report a bug or request a feature?**
Open an issue using the templates in the repository. Helpful bug reports include the URL, mode,
OS, app version, and relevant log lines. See [CONTRIBUTING.md](../CONTRIBUTING.md).
