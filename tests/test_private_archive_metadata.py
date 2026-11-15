"""Archive recovery metadata belongs to the app profile, outside download files."""
import sqlite3
from pathlib import Path

import pytest

from cyoa_downloader_app.download import website_recovery
from cyoa_downloader_app.storage import archive_checkpoint as module


@pytest.fixture(autouse=True)
def private_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile" / "archive-checkpoints"
    monkeypatch.setattr(module, "_checkpoint_root", lambda: profile, raising=False)
    return profile


def test_checkpoint_and_resume_leave_download_tree_free_of_audit(tmp_path, private_profile):
    folder = tmp_path / "downloads" / "Story 雪"
    folder.mkdir(parents=True)
    asset = folder / "image.png"
    asset.write_bytes(b"exact asset")
    original = module.ArchiveCheckpoint(str(folder), "context", source_url="https://fixture.test/story")
    assert original.put("asset", "https://fixture.test/image", str(asset))
    assert Path(original.path).is_relative_to(private_profile)
    assert not (folder / "_audit").exists()
    resumed = module.ArchiveCheckpoint(str(folder), "context", resume=True)
    assert resumed.compatible
    assert resumed.get("asset", "https://fixture.test/image")["path"] == str(asset)
    assert sorted(path.name for path in folder.iterdir()) == ["image.png"]


def test_private_interrupted_archives_are_discovered_only_under_requested_root(tmp_path):
    root = tmp_path / "downloads"
    folder = root / "Story"
    unrelated = tmp_path / "other" / "Story"
    folder.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    module.ArchiveCheckpoint(str(folder), "context", source_url="https://fixture.test/story")
    module.ArchiveCheckpoint(str(unrelated), "context", source_url="https://fixture.test/unrelated")
    assert list(website_recovery._interrupted_archives(root)) == [(folder, "https://fixture.test/story")]
    assert website_recovery.has_website_recovery_work(str(root))
    (folder / "archive_manifest.json").write_text("{}", encoding="utf-8")
    assert list(website_recovery._interrupted_archives(root)) == []


def test_legacy_checkpoint_moves_out_without_losing_resume_or_unrelated_files(tmp_path):
    folder = tmp_path / "downloads" / "Story"
    legacy = folder / "_audit"
    legacy.mkdir(parents=True)
    database = legacy / "archive-checkpoints.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE owner (id INTEGER PRIMARY KEY, generation TEXT, context TEXT)")
        db.execute("INSERT INTO owner VALUES (1,'old','context')")
        db.execute("CREATE TABLE records (kind TEXT, url TEXT, data TEXT, PRIMARY KEY(kind,url))")
        db.execute("CREATE TABLE archive (id INTEGER PRIMARY KEY, source TEXT)")
        db.execute("INSERT INTO archive VALUES (1,'https://fixture.test/story')")
    db.close()
    unrelated = legacy / "authored.txt"
    unrelated.write_text("keep", encoding="utf-8")
    checkpoint = module.ArchiveCheckpoint(str(folder), "context", resume=True)
    assert checkpoint.compatible
    assert not database.exists()
    assert unrelated.read_text("utf-8") == "keep"
    assert module.ArchiveCheckpoint.source(checkpoint.path) == "https://fixture.test/story"


def test_identical_titles_in_different_output_roots_have_separate_checkpoints(tmp_path):
    first, second = tmp_path / "one" / "Story", tmp_path / "two" / "Story"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    a = module.ArchiveCheckpoint(str(first), "context", source_url="https://fixture.test/one")
    b = module.ArchiveCheckpoint(str(second), "context", source_url="https://fixture.test/two")
    assert a.path != b.path
    assert module.ArchiveCheckpoint.source(a.path) != module.ArchiveCheckpoint.source(b.path)


def test_migrating_last_owned_file_removes_empty_legacy_audit_directory(tmp_path):
    folder = tmp_path / "Story"
    folder.mkdir()
    asset = folder / "image.png"
    asset.write_bytes(b"verified bytes")
    old = module.ArchiveCheckpoint(str(folder), "context", source_url="https://fixture.test/story")
    old.put("asset", "https://fixture.test/image", str(asset))
    legacy = folder / "_audit"
    legacy.mkdir()
    Path(old.path).replace(legacy / "archive-checkpoints.sqlite3")
    current = module.ArchiveCheckpoint(str(folder), "context", resume=True)
    assert current.compatible
    assert current.get("asset", "https://fixture.test/image")["path"] == str(asset)
    assert not legacy.exists()


def test_failed_migration_preserves_original_checkpoint(tmp_path, private_profile, monkeypatch):
    folder = tmp_path / "Story"
    folder.mkdir()
    old = module.ArchiveCheckpoint(str(folder), "context", source_url="https://fixture.test/story")
    legacy = folder / "_audit"
    legacy.mkdir()
    database = legacy / "archive-checkpoints.sqlite3"
    Path(old.path).replace(database)
    original = database.read_bytes()

    def fail_replace(*_args):
        raise OSError("fixture: failed atomic migration")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="failed atomic migration"):
        module.ArchiveCheckpoint(str(folder), "context", resume=True)
    assert database.read_bytes() == original
    assert not Path(old.path).exists()
    assert not list(private_profile.glob("*.tmp"))


def test_private_discovery_rejects_checkpoint_pointing_to_another_folder(tmp_path):
    root = tmp_path / "downloads"
    first, second = root / "One", root / "Two"
    first.mkdir(parents=True)
    second.mkdir()
    checkpoint = module.ArchiveCheckpoint(str(first), "context", source_url="https://fixture.test/story")
    with sqlite3.connect(checkpoint.path) as db:
        db.execute("UPDATE archive SET folder=?", (str(second),))
    db.close()
    assert list(website_recovery._interrupted_archives(root)) == []


def test_owned_queue_publication_preserves_private_resume_metadata(tmp_path):
    from tools.run_live_queue import publish_job_output

    root = tmp_path / "downloads"
    staging = root / "_audit" / "work" / "job" / "attempt"
    source = staging / "content"
    source.mkdir(parents=True)
    asset = source / "index.html"
    asset.write_bytes(b"<h1>Exact story</h1>")
    checkpoint = module.ArchiveCheckpoint(str(source), "context", source_url="https://fixture.test/story")
    checkpoint.put("route", "https://fixture.test/story", str(asset))
    destination = root / "Story"
    publish_job_output({"folder": "Story", "output": str(destination), "staging": str(staging)}, root)
    resumed = module.ArchiveCheckpoint(str(destination), "context", resume=True)
    assert resumed.compatible
    assert resumed.get("route", "https://fixture.test/story")["path"] == str(destination / "index.html")
    assert not (destination / "_audit").exists()
    assert not Path(checkpoint.path).exists()
