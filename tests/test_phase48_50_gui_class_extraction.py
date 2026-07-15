import inspect


def test_gui_class_body_moved_to_gui_app():
    import cyoa_downloader

    cls = cyoa_downloader.CYOADownloaderGUI
    assert cls.__module__ == "cyoa_downloader_app.gui.app"
    assert hasattr(cls, "_setup_ui")
    assert hasattr(cls, "_v46_poll_progress")


def test_gui_patch_gate_still_records_final_order():
    import cyoa_downloader
    from cyoa_downloader_app.gui.patches import PATCH_ORDER, applied_patch_order

    assert applied_patch_order(cyoa_downloader.CYOADownloaderGUI) == PATCH_ORDER


def test_legacy_no_longer_defines_gui_class_body():
    from cyoa_downloader_app.runtime import surface as legacy

    source = inspect.getsource(legacy)
    assert "class CYOADownloaderGUI:" not in source
    assert "moved `CYOADownloaderGUI` class body" in source
