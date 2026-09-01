from pathlib import Path

from cyoa_downloader_app.gui.app import CYOADownloaderGUI, _mode_label
from cyoa_downloader_app.cli import (
    _batch_website_zip_output,
    _configured_ytdlp_cookie_path,
)
from cyoa_downloader_app.importers.batch import (
    export_queue_items_to_file,
    import_queue_items_from_file,
)


class _FakeBadge:
    def __init__(self):
        self.values = {}

    def configure(self, **kwargs):
        self.values.update(kwargs)


def test_queue_mode_can_change_in_place():
    gui = CYOADownloaderGUI.__new__(CYOADownloaderGUI)
    item = {"url": "https://example.test/cyoa/", "mode": "auto"}
    gui._queue_data = [item]
    badge = _FakeBadge()

    gui._set_queue_item_mode(item, "website_folder", badge)

    assert item["mode"] == "website_folder"
    assert badge.values["text"] == "ICC Folder"
    assert badge.values["fg_color"] == gui.BADGE_COLORS["website_folder"][0]


def test_mode_label_localizes_internal_website_mode_as_icc():
    assert _mode_label("website_folder", "en") == "ICC Folder"
    assert _mode_label("website_folder", "id") == "Folder ICC"
    assert _mode_label("website_zip", "en") == "ICC ZIP"
    assert _mode_label("website_zip", "id") == "ZIP ICC"


def test_queue_export_round_trips_url_filename_and_mode(tmp_path: Path):
    items = [
        {
            "url": "https://example.test/a/",
            "filename": "A",
            "mode": "website_folder",
            "_queue_id": "internal-id",
        },
        {"url": "https://example.test/b/", "filename": "", "mode": "auto"},
    ]

    for extension in (".csv", ".txt"):
        path = tmp_path / f"queue{extension}"
        assert export_queue_items_to_file(items, str(path)) == 2
        assert import_queue_items_from_file(str(path)) == [
            {"url": "https://example.test/a/", "filename": "A", "mode": "website_folder"},
            {"url": "https://example.test/b/", "filename": "", "mode": "auto"},
        ]


def test_cli_batch_auto_inherits_global_folder_output():
    assert _batch_website_zip_output("auto", False) is False
    assert _batch_website_zip_output("", False) is False
    assert _batch_website_zip_output("auto", True) is True
    assert _batch_website_zip_output("website_zip", False) is True
    assert _batch_website_zip_output("website_folder", True) is False


def test_cli_uses_saved_ytdlp_cookie_file(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    assert _configured_ytdlp_cookie_path("", str(cookie_file)) == str(cookie_file)
    assert _configured_ytdlp_cookie_path(str(cookie_file), "missing") == str(cookie_file)
    assert _configured_ytdlp_cookie_path("", str(tmp_path / "missing.txt")) == ""

    import pytest
    with pytest.raises(FileNotFoundError):
        _configured_ytdlp_cookie_path(str(tmp_path / "missing.txt"), "")
