import json
import sqlite3
from pathlib import Path

import pytest

from cyoa_downloader_app.integrations import cyoa_manager
from cyoa_downloader_app.integrations.offline_viewers.injector import _localize_preserved_index_assets
from cyoa_downloader_app.preview_assets import _BUNDLED_INTCYOAENHANCER_USERSCRIPT


def test_manager_list_includes_local_entries_and_project_json_url(tmp_path):
    db = tmp_path / "library.sqlite3"
    local_project = tmp_path / "project.json"
    local_project.write_text(json.dumps({"rows": []}), encoding="utf-8")
    assert cyoa_manager.add_to_cyoa_manager(str(local_project), db_path=str(db)) is True
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO library_projects (id,name,source_url,project_json_url,file_path) "
            "VALUES (?,?,?,?,?)",
            ("remote", "Remote", "", "https://example.com/project.json", ""),
        )
    projects = cyoa_manager._list_cyoa_manager_projects(str(db))
    assert len(projects) == 2
    assert projects[0]["file_path"] == str(local_project)
    assert projects[0]["source_url"] == ""
    assert projects[1]["source_url"] == "https://example.com/project.json"


def test_manager_list_accepts_older_library_without_project_json_url(tmp_path):
    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE library_projects (id TEXT, name TEXT, source_url TEXT, file_path TEXT)")
        conn.execute("INSERT INTO library_projects VALUES ('one','Local','','C:/project.json')")
    projects = cyoa_manager._list_cyoa_manager_projects(str(db))
    assert len(projects) == 1
    assert projects[0]["project_json_url"] == ""
    assert projects[0]["file_path"] == "C:/project.json"


def test_manager_serve_uses_existing_offline_folder(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(json.dumps({"rows": []}), encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assert cyoa_manager.prepare_cyoa_manager_serve_folder(str(project)) == str(tmp_path)


def test_manager_serve_builds_once_and_reuses_local_assets(tmp_path, monkeypatch):
    project = tmp_path / "source" / "project.json"
    project.parent.mkdir()
    project.write_text(json.dumps({"rows": [], "pointTypes": [], "styling": {}, "version": "2.1"}), encoding="utf-8")
    image = project.parent / "images" / "a.png"
    image.parent.mkdir()
    image.write_bytes(b"image")
    extra = project.parent / "assets" / "cover.webp"
    extra.parent.mkdir()
    extra.write_bytes(b"cover")
    (project.parent / "background.png").write_bytes(b"background")
    monkeypatch.setattr(cyoa_manager.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    from cyoa_downloader_app.integrations.offline_viewers import injector, registry

    monkeypatch.setattr(registry, "_auto_register_bundled_viewers", lambda: None)
    monkeypatch.setattr(registry, "get_viewer_for_site", lambda *_args, **_kwargs: {"id": "local"})
    calls = []

    def fake_inject(output_dir, _data, _viewer, **kwargs):
        calls.append(kwargs)
        target = Path(output_dir) / "manager_offline"
        target.mkdir()
        (target / "index.html").write_text("<html></html>", encoding="utf-8")
        return str(target / "index.html")

    monkeypatch.setattr(injector, "_apply_offline_viewer", fake_inject)
    first = cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    second = cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    assert first == second
    assert len(calls) == 1
    assert calls[0]["asset_source_dirs"] == {
        "images": str(image.parent), "assets": str(extra.parent),
    }
    assert (Path(first) / "background.png").read_bytes() == b"background"


def test_manager_preview_asset_cache_is_scoped_to_each_project(tmp_path, monkeypatch):
    from cyoa_downloader_app.integrations.offline_viewers import injector, registry

    monkeypatch.setattr(cyoa_manager.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(registry, "_auto_register_bundled_viewers", lambda: None)
    monkeypatch.setattr(registry, "get_viewer_for_site", lambda *_args, **_kwargs: {"id": "local"})
    cache_paths = []

    def fake_inject(output_dir, _data, _viewer, **kwargs):
        cache_paths.append(Path(kwargs["preserved_asset_cache_dir"]))
        target = Path(output_dir) / "manager_offline"
        target.mkdir()
        (target / "index.html").write_text("<html></html>", encoding="utf-8")
        return str(target / "index.html")

    monkeypatch.setattr(injector, "_apply_offline_viewer", fake_inject)
    for name in ("first", "second"):
        project = tmp_path / name / "project.json"
        project.parent.mkdir()
        project.write_text(json.dumps({"rows": []}), encoding="utf-8")
        cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    assert cache_paths[0] != cache_paths[1]


def test_preserved_asset_cache_avoids_repeat_fetch(tmp_path):
    cached = tmp_path / "__source_assets__" / "cdn.example.com" / "style.css"
    cached.parent.mkdir(parents=True)
    cached.write_text("body{color:red}", encoding="utf-8")
    requested = []

    def fail_fetch(url, **_kwargs):
        requested.append(url)
        raise AssertionError("cached asset fetched again")

    html = '<link rel="stylesheet" href="https://cdn.example.com/style.css">'
    localized = _localize_preserved_index_assets(html, "", str(tmp_path), fetcher=fail_fetch)
    assert "__source_assets__/cdn.example.com/style.css" in localized
    assert requested == []


def test_serve_cheat_restores_rows_objects_scores_and_disabled_buttons():
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content("<html><head></head><body><button id='locked' disabled>Locked</button><button class='choice' onclick='window.choiceClicks=(window.choiceClicks||0)+1'>Choice</button></body></html>")
                page.evaluate("""() => { window.debugApp = {
                    rows:[{allowedChoices:2,allowedChoicesChange:3,isEditModeOn:true,
                           objects:[{title:'<b>Option One</b>',isActive:false,multipleUseVariable:0,
                                     requireds:['x'],isNotSelectable:true,
                                     scores:[{requireds:['y']}]}]}],
                    pointTypes:[{id:'p',startingSum:5}]
                }; }""")
                page.add_script_tag(content=_BUNDLED_INTCYOAENHANCER_USERSCRIPT)
                page.evaluate("() => window.IntCyoaEnhancerCheat.openCheat()")
                assert page.locator("[data-unlimited]").count() == 1
                page.locator("[data-filter]").fill("missing")
                assert page.locator("[data-choice-row]").is_visible() is False
                page.locator("[data-filter]").fill("Option")
                assert page.locator("[data-choice-row]").is_visible() is True
                page.locator("[data-choice]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is True
                page.locator("[data-choice]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is False
                page.locator("[data-unlock]").click()
                page.locator("[data-unlimited]").click()
                state = page.evaluate("""() => ({row:window.debugApp.rows[0],
                    disabled:document.querySelector('#locked').disabled})""")
                assert state["row"]["allowedChoices"] == 0
                assert state["row"]["objects"][0]["requireds"] == []
                assert state["disabled"] is False
                page.evaluate("() => window.IntCyoaEnhancerCheat.setPoint('p', 99)")
                assert page.evaluate("() => window.debugApp.pointTypes[0].startingSum") == 99
                with pytest.raises(Exception):
                    page.evaluate("() => window.IntCyoaEnhancerCheat.setPoint('p', 'bad')")
                assert page.evaluate("() => window.debugApp.pointTypes[0].startingSum") == 99
                page.locator("[data-restore]").click()
                page.locator("[data-resetpts]").click()
                restored = page.evaluate("""() => ({row:window.debugApp.rows[0],
                    disabled:document.querySelector('#locked').disabled,
                    point:window.debugApp.pointTypes[0].startingSum})""")
                assert restored["row"]["allowedChoices"] == 2
                assert restored["row"]["allowedChoicesChange"] == 3
                assert restored["row"]["isEditModeOn"] is True
                assert restored["row"]["objects"][0]["requireds"] == ["x"]
                assert restored["row"]["objects"][0]["scores"][0]["requireds"] == ["y"]
                assert restored["disabled"] is True
                assert restored["point"] == 5
                page.locator("[data-reveal]").click()
                assert page.locator("#locked").is_disabled() is False
                page.locator("[data-reveal]").click()
                assert page.locator("#locked").is_disabled() is True
                page.locator("[data-selectall]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is True
                assert page.evaluate("() => window.choiceClicks || 0") == 0
                page.locator("[data-resetchoices]").click()
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].isActive") is False
                assert page.evaluate("() => window.debugApp.rows[0].objects[0].multipleUseVariable") == 0
            finally:
                browser.close()
    except playwright.Error as exc:
        pytest.skip(f"Chromium unavailable: {exc}")
