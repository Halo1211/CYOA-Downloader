import json
import sqlite3
import zipfile
from pathlib import Path

from cyoa_downloader_app.integrations.cyoa_manager import (
    add_archive_to_cyoa_manager,
    add_to_cyoa_manager,
)


def test_manager_registers_legacy_project_with_bundled_original_viewer(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(json.dumps({"rows": []}), encoding="utf-8")
    db = tmp_path / "library.sqlite3"
    assert add_to_cyoa_manager(str(project), db_path=str(db)) is True
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT viewer_preference, project_json_url FROM library_projects"
        ).fetchone()
    assert row == ("icc-original", None)


def test_manager_registers_plus_project_and_migrates_old_table(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(json.dumps({"version": "2.10.4", "rows": [], "styling": {}}), encoding="utf-8")
    db = tmp_path / "library.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE library_projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT, cover_image TEXT,
            source_url TEXT, file_path TEXT NOT NULL, viewer_preference TEXT,
            favorite INTEGER, exclude_from_perk_index INTEGER,
            date_added TEXT, tags_json TEXT)""")
    assert add_to_cyoa_manager(str(project), source_url="https://example.com/project.json", db_path=str(db)) is True
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT viewer_preference, project_json_url FROM library_projects"
        ).fetchone()
    assert row == ("icc2-plus", "https://example.com/project.json")


def test_manager_imports_zip_as_stable_project_folder(tmp_path):
    archive = tmp_path / "demo.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("project.json", json.dumps({"rows": [{"image": "images/a.png"}]}))
        zip_file.writestr("images/a.png", b"png")
    db = tmp_path / "library.sqlite3"
    assert add_archive_to_cyoa_manager(str(archive), db_path=str(db)) is True
    with sqlite3.connect(db) as connection:
        path = connection.execute("SELECT file_path FROM library_projects").fetchone()[0]
    assert (tmp_path / "demo_manager" / "project.json").is_file()
    assert (tmp_path / "demo_manager" / "images" / "a.png").read_bytes() == b"png"
    assert path == str(tmp_path / "demo_manager" / "project.json")


def test_manager_reimport_changed_zip_does_not_register_stale_project(tmp_path):
    archive = tmp_path / "demo.zip"
    db = tmp_path / "library.sqlite3"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("project.json", json.dumps({"rows": [], "version": "1"}))
    assert add_archive_to_cyoa_manager(str(archive), db_path=str(db)) is True
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("project.json", json.dumps({"rows": [], "version": "2"}))
    assert add_archive_to_cyoa_manager(str(archive), db_path=str(db)) is True
    with sqlite3.connect(db) as connection:
        paths = [row[0] for row in connection.execute("SELECT file_path FROM library_projects")]
    assert len(paths) == 2
    assert sorted(json.loads(Path(path).read_text(encoding="utf-8"))["version"] for path in paths) == ["1", "2"]


def test_manager_rejects_zip_bomb_before_extraction(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("project.json", json.dumps({"rows": []}))
        zip_file.writestr("images/huge.txt", b"0" * (2 * 1024 * 1024))
    assert add_archive_to_cyoa_manager(str(archive), db_path=str(tmp_path / "db.sqlite3")) is False
    assert not (tmp_path / "bomb_manager").exists()
