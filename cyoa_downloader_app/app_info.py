"""Application metadata and stable defaults."""

_APP_DISPLAY_NAME = "CYOA Downloader"
_APP_VERSION = "1.1.3"
_STABILIZATION_PATCH_ID = "CYOA-v1.1.3"
_GITHUB_RELEASE_API = "https://api.github.com/repos/Halo1211/CYOA-Downloader/releases/latest"

DEFAULT_WAIT_TIME = 60
DEFAULT_MAX_WORKERS = 4

__all__ = [
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_WAIT_TIME",
    "_APP_DISPLAY_NAME",
    "_APP_VERSION",
    "_GITHUB_RELEASE_API",
    "_STABILIZATION_PATCH_ID",
]
