"""Conservative data-file selection for complete application bundles.

Keep optional runtime integrations and their public helpers. Only identified
development-test trees and Selenium's foreign-platform manager executables
are omitted. Unknown platforms are left intact.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def keep_bundle_member(name: str, platform: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if parts[:2] == ("pandas", "tests") or parts == ("pandas", "conftest.py"):
        return False
    selected = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(platform)
    if (selected and len(parts) > 3 and parts[:3] == ("selenium", "webdriver", "common")
            and parts[3] in {"windows", "macos", "linux"}):
        return parts[3] == selected
    return True
