CYOA Downloader v1.0 Release — First Run
=========================================

If you downloaded a Windows EXE package:

1. Extract the ZIP first.
2. Open the extracted folder.
3. Run "CYOA Downloader.exe".
4. If the app does not start, check docs/TROUBLESHOOTING.md.

If you downloaded the source code:

Windows PowerShell:
  py -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python cyoa_downloader.py

Linux/macOS:
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python cyoa_downloader.py

Verification commands:
  python cyoa_downloader.py --dependency-check
  python cyoa_downloader.py --self-test
  python cyoa_downloader.py --help

Main ICC commands:
  python cyoa_downloader.py --icc "URL" -o output
  python cyoa_downloader.py --icc-folder "URL" -o output_folder --serve

Read INSTALLATION.md for the full step-by-step guide.
