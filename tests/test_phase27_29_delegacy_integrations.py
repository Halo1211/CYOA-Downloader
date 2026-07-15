import os
import sqlite3
import tempfile

from cyoa_downloader_app.runtime import surface as legacy
from cyoa_downloader_app.diagnostics.self_test import run_internal_self_test
from cyoa_downloader_app.integrations import cyoa_manager, gallery_dl


def test_phase27_cyoa_manager_real_module_round_trip(tmp_path):
    project = tmp_path / "project.json"
    project.write_text('{"rows": []}', encoding="utf-8")
    db = tmp_path / "library.sqlite3"

    assert cyoa_manager._cyoa_manager_viewer_pref("icc_remix") == "icc-remix"
    assert cyoa_manager._cyoa_manager_viewer_pref("standard") == "icc2-plus"

    added = cyoa_manager.add_to_cyoa_manager(
        str(project),
        name="Demo",
        source_url="https://example.com/cyoa",
        tags=["test"],
        db_path=str(db),
    )
    assert added is True
    assert cyoa_manager.add_to_cyoa_manager(str(project), name="Demo", db_path=str(db)) is None

    rows = cyoa_manager._list_cyoa_manager_projects(str(db))
    assert len(rows) == 1
    assert rows[0]["name"] == "Demo"
    assert rows[0]["source_url"] == "https://example.com/cyoa"

    with sqlite3.connect(db) as con:
        count = con.execute("select count(*) from library_projects").fetchone()[0]
    assert count == 1


def test_phase28_gallery_dl_real_module_state_and_candidate_sync():
    old_mode = gallery_dl._gallery_dl_mode
    old_path = gallery_dl._gallery_dl_path
    old_config = gallery_dl._gallery_dl_config
    try:
        gallery_dl._set_gallery_dl_mode("force", path="gallery-dl-test", config="")
        assert gallery_dl._gallery_dl_mode == "force"
        assert legacy._gallery_dl_mode == "force"
        assert gallery_dl._is_gallery_dl_candidate("https://example.com/gallery/abc") == "example.com"

        gallery_dl._set_gallery_dl_mode("smart")
        assert gallery_dl._is_gallery_dl_candidate("https://i.pximg.net/img-original/img/abc.jpg") is None
        assert gallery_dl._is_gallery_dl_candidate("https://www.pixiv.net/artworks/123") == "pixiv"

        gallery_dl._set_gallery_dl_mode("off")
        assert gallery_dl._is_gallery_dl_candidate("https://www.pixiv.net/artworks/123") is None
    finally:
        gallery_dl._set_gallery_dl_mode(old_mode, path=old_path, config=old_config)


def test_phase29_self_test_moved_out_of_legacy_and_alias_preserved():
    assert run_internal_self_test.__module__ == "cyoa_downloader_app.diagnostics.self_test"
    assert legacy.run_internal_self_test is run_internal_self_test
