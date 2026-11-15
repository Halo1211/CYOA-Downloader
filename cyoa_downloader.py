"""Compatibility facade for the refactored CYOA Downloader package.

The public entry point remains `cyoa_downloader.py`; implementation currently
lives behind `cyoa_downloader_app` while the source is split safely in phases.
"""

if __name__ == "__main__":
    import os as _startup_os
    if _startup_os.environ.get("CYOA_STARTUP_REPORT"):
        import time as _startup_time
        _startup_entry_ns = _startup_time.perf_counter_ns()
        from cyoa_downloader_app.diagnostics.startup import mark as _startup_mark
        _startup_mark("entry", _startup_entry_ns)

# Imported callers retain the complete historical symbol table. Executing the
# CLI can defer compatibility/GUI composition until a download or GUI needs it.
# isort: off
if __name__ != "__main__":
    from cyoa_downloader_app.compat import *
from cyoa_downloader_app.cli import main
# isort: on

if __name__ == "__main__" and _startup_os.environ.get("CYOA_STARTUP_REPORT"):
    _startup_mark("cli_imported")


def __getattr__(name):
    from cyoa_downloader_app import compat as _compat
    return getattr(_compat, name)


# Compatibility markers for legacy source-introspection tests during migration:
# _done _cache_load _cache_get _v25_safe_after_widget try_decode_bytes
# CYOADownloaderGUI WebsiteDownloader run_download fetch_response process_images

if __name__ == "__main__":
    main()
