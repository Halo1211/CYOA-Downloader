"""End-to-end boundaries for itch-dl HTML5 archive preparation."""

import zipfile
from pathlib import Path

import pytest

from cyoa_downloader_app.integrations import itch
from cyoa_downloader_app.integrations.itch_offline import (
    EncryptedHtml5ArchiveError,
    materialize_itch_html5_archive,
)


def _make_html5_zip(path: Path, html: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.html", html)
        archive.writestr("js/app.js", "window.gameLoaded = true")


def test_html5_archive_localizes_assets_and_reuses_completed_folder(tmp_path):
    archive = tmp_path / "game.zip"
    _make_html5_zip(
        archive,
        '<link rel="stylesheet" href="https://cdn.example.test/theme.css">'
        '<script src="js/app.js"></script>',
    )
    fetched = []

    class Response:
        status_code = 200

        def __init__(self, content, content_type):
            self.content = content
            self.headers = {"Content-Type": content_type}

        def close(self):
            pass

    def fetch(url, **_kwargs):
        fetched.append(url)
        if url.endswith("theme.css"):
            return Response(b'body{src:url("font.woff2")}', "text/css")
        if url.endswith("font.woff2"):
            return Response(b"local-font", "font/woff2")
        raise AssertionError(f"Unexpected fetch: {url}")

    index, reused, complete = materialize_itch_html5_archive(
        archive, "https://creator.itch.io/game", fetcher=fetch,
    )
    assert index and not reused and complete
    html = Path(index).read_text(encoding="utf-8")
    assert "https://cdn.example.test" not in html
    assert (Path(index).parent / "js" / "app.js").is_file()
    assert list(Path(index).parent.rglob("font.woff2"))[0].read_bytes() == b"local-font"
    assert fetched == ["https://cdn.example.test/theme.css",
                       "https://cdn.example.test/font.woff2"]
    assert materialize_itch_html5_archive(
        archive, "https://creator.itch.io/game", fetcher=fetch,
    ) == (index, True, True)
    assert len(fetched) == 2


def test_non_html5_and_unsafe_archives_do_not_create_offline_folder(tmp_path):
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", "text")
    assert materialize_itch_html5_archive(plain, "https://creator.itch.io/game") == (
        None, False, True,
    )
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        archive.writestr("../escape.txt", "unsafe")
    with pytest.raises(ValueError, match="Unsafe archive path"):
        materialize_itch_html5_archive(unsafe, "https://creator.itch.io/game")
    assert not (tmp_path / "unsafe_offline").exists()


def test_duplicate_archive_paths_are_rejected(tmp_path):
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("index.html", "<html></html>")
        archive.writestr("JS/app.js", "first")
        archive.writestr("js/app.js", "second")
    with pytest.raises(ValueError, match="duplicate paths"):
        materialize_itch_html5_archive(archive_path, "https://creator.itch.io/game")
    assert not (tmp_path / "duplicate_offline").exists()


def test_partial_offline_build_retries_missing_assets(tmp_path):
    archive = tmp_path / "partial.zip"
    _make_html5_zip(archive, '<script src="https://cdn.example.test/extra.js"></script>')
    calls = []

    def fetch(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return None

        class Response:
            status_code = 200
            content = b"window.extraLoaded = true"

            def __init__(self):
                self.headers = {"Content-Type": "application/javascript"}

            def close(self):
                pass

        return Response()

    first, reused, complete = materialize_itch_html5_archive(
        archive, "https://creator.itch.io/game", fetcher=fetch,
    )
    assert first and not reused and not complete
    second, reused, complete = materialize_itch_html5_archive(
        archive, "https://creator.itch.io/game", fetcher=fetch,
    )
    assert second == first and not reused and complete
    assert len(calls) == 2
    assert not (Path(second).parent / "failed_assets.txt").exists()


def test_password_protected_html5_zip_retains_original(tmp_path):
    archive = tmp_path / "encrypted.zip"
    _make_html5_zip(archive, "<html></html>")
    data = bytearray(archive.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        offset = data.index(signature)
        data[offset + flag_offset] |= 1
    archive.write_bytes(data)
    with pytest.raises(EncryptedHtml5ArchiveError, match="password-protected"):
        materialize_itch_html5_archive(archive, "https://creator.itch.io/game")
    assert archive.is_file()
    assert not (tmp_path / "encrypted_offline").exists()


def test_itch_download_reports_offline_folder_without_counting_its_files(tmp_path, monkeypatch):
    monkeypatch.setattr(itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))

    def fake_run(command, **_kwargs):
        destination = Path(command[command.index("--download-to") + 1])
        archive = destination / "game.zip"
        if not archive.exists():
            _make_html5_zip(archive, '<script src="js/app.js"></script>')
        return 0, "complete"

    monkeypatch.setattr(itch, "_run_itch_process", fake_run)
    first = itch.download_itch_assets(
        "https://creator.itch.io/game", str(tmp_path), mirror_web=True,
    )
    assert first["ok"] and first["saved"] == 1
    assert first["offline_ready"] == 1
    assert Path(first["offline_entries"][0]).is_file()
    second = itch.download_itch_assets(
        "https://creator.itch.io/game", str(tmp_path), mirror_web=True,
    )
    assert second["ok"] and second["saved"] == 0 and second["existing"] == 1
    assert second["offline_cached"] == 1


def test_itch_download_reports_encrypted_zip_without_removing_download(tmp_path, monkeypatch):
    monkeypatch.setattr(itch, "detect_itch_backend", lambda: (["itch-dl"], "itch-dl (PATH)"))
    monkeypatch.setattr(itch, "_resolve_itch_api_key", lambda _explicit: (None, "none"))

    def fake_run(command, **_kwargs):
        destination = Path(command[command.index("--download-to") + 1])
        archive = destination / "game.zip"
        _make_html5_zip(archive, "<html></html>")
        data = bytearray(archive.read_bytes())
        for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            offset = data.index(signature)
            data[offset + flag_offset] |= 1
        archive.write_bytes(data)
        return 0, "complete"

    monkeypatch.setattr(itch, "_run_itch_process", fake_run)
    result = itch.download_itch_assets(
        "https://creator.itch.io/game", str(tmp_path), mirror_web=True,
    )
    assert result["ok"] and result["offline_encrypted"] == 1
    assert (tmp_path / "itch_assets" / "game.zip").is_file()
    assert result["offline_entries"] == []
