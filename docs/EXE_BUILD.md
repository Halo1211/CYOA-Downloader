# Windows EXE Build Guide — CYOA Downloader v1.0 Release

This guide explains the recommended way to build a Windows executable release.

---

## 1. Recommended distribution format

Prefer a zipped folder:

```text
CYOA-Downloader-v1.0-Windows-x64.zip
├─ CYOA Downloader.exe
├─ assets/
│  ├─ logo-light.png
│  └─ logo-dark.png
├─ README_FIRST.txt
└─ LICENSE
```

This is usually easier to debug and less likely to trigger false-positive antivirus behavior than a single-file packed EXE.

---

## 2. Install build dependencies

```powershell
py -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

Optional recovery dependencies can also be installed before building if you want them bundled:

```powershell
pip install json5 httpx[h2] tldextract cloudscraper selenium yt-dlp gallery-dl keyring pandas openpyxl xlrd pillow
```

---

## 3. Recommended onedir build

```powershell
pyinstaller ^
  --noconfirm ^
  --windowed ^
  --name "CYOA Downloader" ^
  --add-data "assets;assets" ^
  cyoa_downloader.py
```

Output:

```text
dist/CYOA Downloader/CYOA Downloader.exe
```

If an `.ico` file is added later:

```powershell
pyinstaller ^
  --noconfirm ^
  --windowed ^
  --name "CYOA Downloader" ^
  --icon assets/app.ico ^
  --add-data "assets;assets" ^
  cyoa_downloader.py
```

---

## 4. Onefile build, optional

```powershell
pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "CYOA Downloader" ^
  --add-data "assets;assets" ^
  cyoa_downloader.py
```

Trade-offs:

- slower startup;
- harder to debug;
- higher false-positive antivirus risk;
- asset path handling must be tested carefully.

---
