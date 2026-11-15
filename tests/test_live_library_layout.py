"""Readable queue names, staged publication, and recoverable library moves."""

import csv
import functools
import json
import os
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import organize_live_library as organize
from tools import run_live_queue as queue


def old_library(root, titles=("Story [CYOA]", "Story [CYOA]")):
    audit = root / "_audit"
    audit.mkdir()
    records = []
    for index, title in enumerate(titles):
        identity = f"{index:010x}"
        folder = f"{title} [{identity}]"
        payload = root / folder / "archive"
        payload.mkdir(parents=True)
        (payload / "index.html").write_text('<img src="images/picture.png">', encoding="utf-8")
        (payload / "images").mkdir()
        (payload / "images/picture.png").write_bytes(b"unchanged image")
        (payload.parent / "cyoa_downloader.log").write_text("old log", encoding="utf-8")
        row = {"id": identity, "title": title, "folder": folder, "entry": folder + "/archive/index.html",
               "url": f"https://example.test/{index}", "status": "downloaded", "mode": "pure_website_folder",
               "started": time.time(), "output": str(payload.parent)}
        records.append(row)
        queue.write_json(audit / (identity + ".json"), row)
        queue.write_json(audit / (identity + ".job.json"), row)
        queue.write_json(audit / (identity + ".viewer.json"), {"entry": row["entry"], "status": "passed"})
    queue.catalogue(root, records, audit)
    return records


def test_queue_titles_have_no_internal_hash_and_preserve_authored_tags(tmp_path):
    source = tmp_path / "queue.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["url", "filename"])
        writer.writeheader()
        for index, title in enumerate(("Story [CYOA]", "Story [CYOA]", "CON", "_audit", "日本語" * 50)):
            writer.writerow({"url": f"https://example.test/{index}", "filename": title})
        writer.writerow({"url": "https://example.test/0", "filename": "duplicate URL"})
    jobs = queue.queue_jobs(source)
    assert [job["folder"] for job in jobs[:4]] == ["Story [CYOA]", "Story [CYOA] (2)", "CYOA CON", "CYOA _audit"]
    assert len(jobs) == 5 and len(jobs[-1]["folder"].encode("utf-8")) <= 95
    assert len(jobs[0]["rows"]) == 2 and all(job["id"] not in job["folder"] for job in jobs)


def test_staged_publication_places_entry_directly_in_title_folder(tmp_path):
    stage = tmp_path / "_audit/work/id/attempt"
    (stage / "content").mkdir(parents=True)
    (stage / "content/index.html").write_text("viewer", encoding="utf-8")
    (stage / "cyoa_downloader.log").write_text("log", encoding="utf-8")
    job = {"folder": "Story", "output": str(tmp_path / "Story"), "staging": str(stage)}
    queue.publish_job_output(job, tmp_path)
    assert (tmp_path / "Story/index.html").read_text() == "viewer"
    assert (stage / "cyoa_downloader.log").exists() and not (tmp_path / "Story/archive").exists()
    (stage / "content").mkdir()
    (stage / "content/index.html").write_text("new", encoding="utf-8")
    with pytest.raises(FileExistsError):
        queue.publish_job_output(job, tmp_path)
    assert (tmp_path / "Story/index.html").read_text() == "viewer"


def test_real_queue_worker_publishes_flat_title_output(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><title>Story</title><p>Offline fixture</p>", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(site)))
    owner = threading.Thread(target=server.serve_forever, daemon=True)
    owner.start()
    root = tmp_path / "library"
    audit = root / "_audit"
    audit.mkdir(parents=True)
    stage = audit / "work/0123456789/attempt"
    stage.mkdir(parents=True)
    profile = tmp_path / "profile"
    profile.mkdir()
    spec = audit / "0123456789.job.json"
    queue.write_json(spec, {"id": "0123456789", "url": f"http://127.0.0.1:{server.server_port}/",
        "mode": "pure_website_folder", "folder": "Story [CYOA]", "output": str(root / "Story [CYOA]"),
        "staging": str(stage)})
    try:
        process = subprocess.run([sys.executable, str(queue.REPO / "tools/run_live_queue.py"), "--worker", str(spec)],
            cwd=queue.REPO, env=dict(os.environ, USERPROFILE=str(profile), PYTHONIOENCODING="utf-8"),
            capture_output=True, timeout=60, check=False)
        assert process.returncode == 0, process.stderr[-3000:].decode("utf-8", "replace")
        assert (root / "Story [CYOA]/index.html").is_file()
        assert not (root / "Story [CYOA]/archive").exists()
        assert not (root / "Story [CYOA]/cyoa_downloader.log").exists()
    finally:
        server.shutdown()
        server.server_close()
        owner.join(2)


def test_existing_checkpoint_is_resumed_and_unrelated_name_is_preserved(tmp_path):
    records = old_library(tmp_path, ("Story",))
    jobs = [{"id": records[0]["id"], "url": records[0]["url"], "title": "Story"},
            {"id": "abcdef1234", "url": "https://new.test/", "title": "Story"}]
    (tmp_path / "Story").mkdir()
    bound = queue.bind_job_folders(jobs, tmp_path, tmp_path / "_audit")
    assert bound[0]["folder"] == records[0]["folder"] and bound[1]["folder"] == "Story (2)"
    records[0]["status"] = "failed"
    queue.write_json(tmp_path / "_audit" / (records[0]["id"] + ".json"), records[0])
    assert queue.bind_job_folders(jobs[:1], tmp_path, tmp_path / "_audit")[0]["folder"] == "Story (2)"


def test_migration_preserves_payload_files_and_updates_all_catalogue_paths(tmp_path):
    records = old_library(tmp_path)
    before = [organize.inventory((tmp_path / row["entry"]).parent) for row in records]
    plan = organize.plan_layout(tmp_path, records)
    result = organize.apply_layout(tmp_path, records, plan)
    assert result["changed"] == 2 and result["files_verified"] == 4
    saved = json.loads((tmp_path / "_audit/queue_status.json").read_text())
    for index, row in enumerate(saved):
        assert row["entry"] == row["folder"] + "/index.html"
        assert row["folder"] == "Story [CYOA]" + (" (2)" if index else "")
        assert organize.inventory((tmp_path / row["entry"]).parent) == before[index]
        report = json.loads((tmp_path / "_audit" / (row["id"] + ".viewer.json")).read_text())
        assert report["entry"] == row["entry"]
        assert not (tmp_path / records[index]["folder"]).exists()
    assert "archive/index.html" not in (tmp_path / "index.html").read_text()
    assert not any(item["changed"] for item in organize.plan_layout(tmp_path, saved))
    assert list((tmp_path / "_audit/layout-backups").rglob("cyoa_downloader.log"))


def test_catalogue_failure_rolls_back_directory_moves_and_metadata(tmp_path, monkeypatch):
    records = old_library(tmp_path)
    original = (tmp_path / "_audit/queue_status.json").read_bytes()
    plan = organize.plan_layout(tmp_path, records)
    def failure(*args):
        raise OSError("fixture disk failure")
    monkeypatch.setattr(organize, "catalogue", failure)
    with pytest.raises(OSError, match="fixture disk failure"):
        organize.apply_layout(tmp_path, records, plan)
    assert (tmp_path / "_audit/queue_status.json").read_bytes() == original
    for row in records:
        assert (tmp_path / row["entry"]).is_file()
        assert (tmp_path / row["folder"] / "cyoa_downloader.log").is_file()


@pytest.mark.parametrize("bad", ["running", "outside", "missing"])
def test_preflight_rejects_active_or_invalid_records(tmp_path, bad):
    records = old_library(tmp_path, ("Story",))
    if bad == "running": records[0]["status"] = "running"
    if bad == "outside": records[0]["entry"] = "../outside/index.html"
    if bad == "missing": (tmp_path / records[0]["entry"]).unlink()
    with pytest.raises(ValueError):
        organize.plan_layout(tmp_path, records)
