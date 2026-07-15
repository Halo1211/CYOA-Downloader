from pathlib import Path

from cyoa_downloader_app.gui.app import CYOADownloaderGUI
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
    assert badge.values["text"] == "website folder"
    assert badge.values["fg_color"] == gui.BADGE_COLORS["website_folder"][0]


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
