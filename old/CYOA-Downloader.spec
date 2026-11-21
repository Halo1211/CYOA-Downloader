# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile build for CYOA Downloader.

The application can run without the optional integrations, so packages are
collected defensively. FFmpeg, Deno, browser payloads, and RAR helpers remain
system dependencies and are intentionally reported by the diagnostic panel.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

from tools.bundle_policy import keep_bundle_member


ROOT = Path(SPEC).parent
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
version_numbers = [int(part) for part in APP_VERSION.split(".")[:4]]
version_numbers.extend([0] * (4 - len(version_numbers)))
version_tuple = tuple(version_numbers[:4])
version_info_path = ROOT / "build" / "CYOA-Downloader-version-info.txt"
version_info_path.parent.mkdir(parents=True, exist_ok=True)
version_info_path.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple!r},
    prodvers={version_tuple!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'CYOA Downloader contributors'),
         StringStruct('FileDescription', 'CYOA Downloader'),
         StringStruct('FileVersion', '{APP_VERSION}'),
         StringStruct('InternalName', 'CYOA Downloader'),
         StringStruct('LegalCopyright', 'Licensed under MIT'),
         StringStruct('OriginalFilename', 'CYOA Downloader.exe'),
         StringStruct('ProductName', 'CYOA Downloader'),
         StringStruct('ProductVersion', '{APP_VERSION}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)\n""",
    encoding="utf-8",
)
datas = [(str(ROOT / "assets"), "assets")]
binaries = []
hiddenimports = []

for package in (
    "requests", "urllib3", "bs4", "tldextract", "json5", "customtkinter",
    "PIL", "pandas", "openpyxl", "xlrd", "keyring", "cloudscraper",
    "selenium", "playwright", "yt_dlp", "yt_dlp_ejs", "browser_cookie3",
    "httpx", "dns", "plyer", "rarfile", "gallery_dl",
):
    try:
        package_datas, package_binaries, package_hiddenimports = collect_all(package)
    except (ImportError, ModuleNotFoundError):
        continue
    datas += [(source, target) for source, target in package_datas
              if keep_bundle_member(str(Path(target) / Path(source).name), sys.platform)]
    binaries += [(source, target) for source, target in package_binaries
                 if keep_bundle_member(str(Path(target) / Path(source).name), sys.platform)]
    # Some packages (notably pandas) expose their entire internal test suite
    # through collect_submodules. Those tests are not runtime dependencies and
    # would make the one-file executable unnecessarily huge.
    hiddenimports += [
        name for name in package_hiddenimports
        if ".tests" not in name and not name.endswith(".tests")
    ]


a = Analysis(
    [str(ROOT / "cyoa_downloader.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The application uses remote AI APIs; it does not run local ML models.
    # Excluding these packages keeps onefile builds from absorbing unrelated
    # packages installed in a maintainer's global Python environment.
    excludes=["torch", "torchvision", "tensorflow", "transformers", "timm", "scipy"],
    noarchive=False,
)
# Hooks can collect the same development resources again. Apply the same
# conservative policy to the final TOCs, preserving all native runtime assets.
a.datas[:] = [entry for entry in a.datas if keep_bundle_member(entry[0], sys.platform)]
a.binaries[:] = [entry for entry in a.binaries if keep_bundle_member(entry[0], sys.platform)]
pyz = PYZ(a.pure)
portable_folder = os.environ.get("CYOA_BUILD_LAYOUT", "onefile") == "onedir"
exe_inputs = [pyz, a.scripts]
if portable_folder:
    exe_inputs.append([])
else:
    exe_inputs.extend([a.binaries, a.datas, []])
exe = EXE(
    *exe_inputs,
    name="CYOA Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    icon=str(ROOT / "assets" / "cyoa_downloader.ico"),
    version=str(version_info_path),
    console=False,
    exclude_binaries=portable_folder,
)
if portable_folder:
    bundle = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=True, name="CYOA Downloader",
    )
