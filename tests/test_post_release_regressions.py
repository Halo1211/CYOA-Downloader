"""Focused regressions found while auditing less common download paths."""

import json
import os
import zipfile
from pathlib import Path

import requests

from cyoa_downloader_app.core.url_utils import _candidate_urls_for_cyoap_asset
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.integrations import cyoa_manager, itch


def test_manager_installer_location_is_detected(monkeypatch):
    expected = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "CYOA Manager", "save", "library.sqlite3",
    )
    assert expected in cyoa_manager._CYOA_MANAGER_DB_CANDIDATES
    monkeypatch.setattr(cyoa_manager.os.path, "exists", lambda path: path == expected)
    assert cyoa_manager._find_cyoa_manager_db() == expected
    assert cyoa_manager._scan_for_cyoa_manager_db() == [expected]


def test_cyoap_root_relative_asset_uses_origin_root():
    candidates = _candidate_urls_for_cyoap_asset(
        "https://example.test/story/", "/images/card.png", "images",
    )
    assert candidates == ["https://example.test/images/card.png"]


def test_missing_root_relative_entry_script_is_retried(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text('<script src="/js/app.js"></script>', encoding="utf-8")
    downloader = WebsiteDownloader("https://example.test/story/", str(tmp_path))
    requested = []
    monkeypatch.setattr(
        downloader, "_download_asset",
        lambda url, **_kwargs: requested.append(url),
    )
    downloader.repair_missing_entry_scripts()
    assert requested == ["/js/app.js"]
    downloader.close()


def test_google_font_repair_rejects_parent_path_from_css(tmp_path, monkeypatch):
    root = tmp_path / "site"
    css_dir = root / "external" / "fonts.googleapis.com"
    css_dir.mkdir(parents=True)
    (css_dir / "font.css").write_text(
        "@font-face{src:url(../fonts.gstatic.com/../../../escaped.woff2)}",
        encoding="utf-8",
    )
    saved = root / "cached.woff2"
    saved.write_bytes(b"font")
    downloader = WebsiteDownloader("https://example.test/story/", str(root))
    requested = []
    monkeypatch.setattr(
        downloader, "_download_asset",
        lambda url, **_kwargs: requested.append(url) or str(saved),
    )
    downloader.repair_missing_google_fonts()
    assert requested == []
    assert not (tmp_path / "escaped.woff2").exists()
    downloader.close()


def test_manager_zip_read_error_returns_failure(tmp_path, monkeypatch):
    archive = tmp_path / "library.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("project.json", json.dumps({"rows": []}))
    original_open = Path.open

    def fail_archive_read(path, *args, **kwargs):
        if path == archive and args and args[0] == "rb":
            raise PermissionError("archive locked")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_archive_read)
    assert cyoa_manager.add_archive_to_cyoa_manager(str(archive), db_path=str(tmp_path / "db")) is False


def test_itch_zero_file_exit_is_not_reported_as_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(itch, "_resolve_itch_api_key", lambda _key: (None, "none"))
    monkeypatch.setattr(itch, "_run_itch_process", lambda *_args, **_kwargs: (0, "done"))
    result = itch.download_itch_assets("https://creator.itch.io/game", str(tmp_path))
    assert result["ok"] is False
    assert "no files" in result["message"].lower()


def test_html_masquerading_as_script_handles_stream_failure(tmp_path, monkeypatch):
    class BrokenResponse:
        def __init__(self):
            self.headers = {"Content-Type": "text/html"}
            self.closed = False

        def __bool__(self):
            return True

        @property
        def content(self):
            raise requests.ConnectionError("stream interrupted")

        def close(self):
            self.closed = True

    response = BrokenResponse()
    downloader = WebsiteDownloader("https://example.test/story/", str(tmp_path))
    monkeypatch.setattr(downloader, "_fetch", lambda _url: response)
    assert downloader._download_asset("https://example.test/story/app.js") is None
    assert response.closed
    assert any("stream interrupted" in item["error"] for item in downloader._failed_items)
    downloader.close()


def test_manager_preview_rebuilds_when_registered_viewer_changes(tmp_path, monkeypatch):
    from cyoa_downloader_app.integrations.offline_viewers import injector, registry

    project = tmp_path / "project.json"
    project.write_text(json.dumps({"rows": []}), encoding="utf-8")
    viewer_dir = tmp_path / "viewers"
    viewer_dir.mkdir()
    viewer_zip = viewer_dir / "viewer.zip"
    viewer_zip.write_bytes(b"first")
    monkeypatch.setattr(cyoa_manager.tempfile, "gettempdir", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(viewer_dir))
    monkeypatch.setattr(registry, "_auto_register_bundled_viewers", lambda: None)
    monkeypatch.setattr(
        registry, "get_viewer_for_site",
        lambda *_args, **_kwargs: {"id": "local", "zip_filename": "viewer.zip"},
    )
    calls = []

    def fake_inject(output_dir, _project_text, _viewer, **_kwargs):
        calls.append(output_dir)
        target = Path(output_dir) / "manager_offline"
        target.mkdir()
        (target / "index.html").write_text("<html></html>", encoding="utf-8")
        return str(target / "index.html")

    monkeypatch.setattr(injector, "_apply_offline_viewer", fake_inject)
    cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    viewer_zip.write_bytes(b"updated-viewer-bundle")
    cyoa_manager.prepare_cyoa_manager_serve_folder(str(project))
    assert len(calls) == 2
    assert calls[0] != calls[1]
