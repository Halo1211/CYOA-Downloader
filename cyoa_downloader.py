"""Compatibility facade for the refactored CYOA Downloader package.

The public entry point remains `cyoa_downloader.py`; implementation currently
lives behind `cyoa_downloader_app` while the source is split safely in phases.
"""

from cyoa_downloader_app.compat import *  # noqa: F401,F403
from cyoa_downloader_app.cli import main  # noqa: F401,E402


def __getattr__(name):
    from cyoa_downloader_app import compat as _compat
    return getattr(_compat, name)


# Compatibility markers for legacy source-introspection tests during migration:
# _done _cache_load _cache_get _v25_safe_after_widget try_decode_bytes
# CYOADownloaderGUI WebsiteDownloader run_download fetch_response process_images

if __name__ == "__main__":
    main()
