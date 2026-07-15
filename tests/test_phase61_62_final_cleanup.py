import threading


def test_phase61_gui_bootstrap_owns_patch_wiring():
    from cyoa_downloader_app.runtime import surface as legacy
    from cyoa_downloader_app.gui.bootstrap import bootstrap_gui_runtime
    from cyoa_downloader_app.gui.final_behaviors import _V469_PROGRESS_STRINGS, _V469_STATE_LABELS_ID

    assert callable(bootstrap_gui_runtime)
    assert _V469_STATE_LABELS_ID["IDLE"] == "SIAP"
    assert _V469_PROGRESS_STRINGS["show_details"]["en"] == "Show Details"
    assert hasattr(legacy.CYOADownloaderGUI, "_v46_poll_progress")
    assert hasattr(legacy.CYOADownloaderGUI, "_v463_arrange_progress_and_log")


def test_phase62_runtime_state_reexport_identity():
    from cyoa_downloader_app.runtime import surface as legacy
    from cyoa_downloader_app.runtime import state

    assert legacy._RUN_DOWNLOAD_LOCK is state._RUN_DOWNLOAD_LOCK
    assert isinstance(state._RUN_DOWNLOAD_LOCK, threading.RLock().__class__)
    assert legacy.DNS_PRESETS["Cloudflare 1.1.1.1"] == "1.1.1.1"
    assert legacy.BEBASDNS_DOH_VARIANTS["default"].startswith("https://")
    assert legacy._domain_backoff is state._domain_backoff


