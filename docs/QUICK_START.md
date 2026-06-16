# Start Here — CYOA Downloader v1.0 Release

This is the shortest path for users who only want to run the program.

## Windows quick install from source

1. Install Python 3.10 or newer.
2. Download or clone this repository.
3. Open PowerShell inside the project folder.
4. Run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

If `py` does not work, use `python` instead:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

## Linux / macOS quick install from source

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python cyoa_downloader.py
```

## First test commands

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --help
```

## First download examples

ICC ZIP:

```bash
python cyoa_downloader.py --icc "URL" -o output
```

ICC Folder:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

ICC Folder with local preview:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder --serve
```

Replace `URL` with the real CYOA/ICC link.

## Need full explanation?

Read [`INSTALLATION.md`](INSTALLATION.md) and [`USAGE.md`](USAGE.md).
