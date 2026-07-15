#!/usr/bin/env python3
"""Smoke-audit the public compatibility surface exported by cyoa_downloader."""
from __future__ import annotations

import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED = [
    "main", "launch_gui", "run_download", "CYOADownloaderGUI", "WebsiteDownloader",
    "fetch_response", "process_images", "get_project_source", "auto_detect_mode",
    "_derive_mode_flags", "_cache_load", "_cache_get", "_v25_safe_after_widget",
    "try_decode_bytes", "userscript_integration_report",
]


def main() -> int:
    mod = importlib.import_module("cyoa_downloader")
    missing = [name for name in REQUIRED if not hasattr(mod, name)]
    print("# Import Surface Audit")
    print()
    print(f"Module: `{mod.__name__}`")
    print(f"Required names: {len(REQUIRED)}")
    print(f"Missing names: {len(missing)}")
    if missing:
        for name in missing:
            print(f"- MISSING `{name}`")
        return 1
    for name in REQUIRED:
        value = getattr(mod, name)
        print(f"- `{name}` -> `{getattr(value, '__module__', type(value).__module__)}`")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
