import importlib
import logging
import queue
from types import SimpleNamespace

import cyoa_downloader


def test_phase42_gui_assets_import_without_forcing_gui_app():
    assets = importlib.import_module("cyoa_downloader_app.gui.assets")
    assert hasattr(assets, "_APP_LOGO_LIGHT_B64")
    assert hasattr(assets, "_APP_LOGO_DARK_B64")
    assert callable(assets._load_logo_images)
    assert callable(assets._load_window_icon_photo)
    assert cyoa_downloader._load_logo_images is assets._load_logo_images


def test_phase43_gui_logging_handler_real_module():
    logging_ui = importlib.import_module("cyoa_downloader_app.gui.logging_ui")
    q = queue.Queue(maxsize=2)
    handler = logging_ui.GUILogHandler(q)
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "hello", (), None)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    handler.emit(record)
    assert q.get_nowait() == "WARNING:hello"
    assert cyoa_downloader.GUILogHandler is logging_ui.GUILogHandler
    assert logging_ui._v465_log_tag(logging.ERROR, "ERROR", "boom") == "ERROR"
    assert logging_ui._v465_log_tag(logging.INFO, "INFO", "downloaded: asset") == "DOWNLOAD"
    assert logging_ui._v465_log_tag(logging.INFO, "INFO", "retry asset 2/4") == "RETRY"
    assert logging_ui._v465_log_tag(logging.INFO, "INFO", "[Settings] policy saved") == "SETTINGS"


def test_gui_log_renderer_colors_timestamp_level_and_semantic_message():
    logging_ui = importlib.import_module("cyoa_downloader_app.gui.logging_ui")

    class FakeText:
        def __init__(self):
            self.parts = []

        def insert(self, _where, text, tag):
            self.parts.append((text, tag))

    fake_text = FakeText()
    logging_ui._v465_insert_log_line(
        SimpleNamespace(_log_txt=fake_text),
        "2026-07-13 17:00:00,000 - INFO - [Auto-detect] browser adapter selected",
    )
    assert [tag for _text, tag in fake_text.parts] == [
        "TIMESTAMP", "SEPARATOR", "LEVEL_INFO", "SEPARATOR", "AUTO",
    ]


def test_phase44_gui_package_exports_are_lazy():
    gui_pkg = importlib.import_module("cyoa_downloader_app.gui")
    assert "CYOADownloaderGUI" in gui_pkg.__all__
    assert "launch_gui" in gui_pkg.__all__
    # Accessing the package itself must not require eager gui.app import during
    # legacy initialization; normal attribute access still resolves lazily.
    assert getattr(gui_pkg, "CYOADownloaderGUI") is cyoa_downloader.CYOADownloaderGUI
