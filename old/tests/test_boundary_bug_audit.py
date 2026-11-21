"""Program boundary regressions with offline, deterministic fixtures."""

import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from cyoa_downloader_app.config import settings
from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.paths import _safe_rel_path
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.core.url_utils import _cyoap_local_path, canonicalize_url
from cyoa_downloader_app.download import package
from cyoa_downloader_app.importers import batch
from cyoa_downloader_app.integrations import itch_offline
from cyoa_downloader_app.network import fetch_base
from cyoa_downloader_app.project.cyoap_vue import _scan_cyoap_assets


def test_verify_deeply_nested_project_reports_failure(tmp_path):
    (tmp_path / "project.json").write_text('{"rows":' + "[" * 20000 + "0" + "]" * 20000 + "}", encoding="utf-8")
    ok, report = package.verify_output_package(str(tmp_path))
    assert not ok
    assert "project.json failed to parse" in report


@pytest.mark.parametrize("path", ["/a/../b/..", "/..", "/a/../../", "/./"])
def test_canonical_url_dot_segments_resolve_to_root(path):
    assert canonicalize_url("https://example.test" + path) == "https://example.test/"


def test_cyoap_path_decodes_percent_escapes_once(tmp_path):
    path = _cyoap_local_path(str(tmp_path), "https://example.test/images/art%2520work.png")
    assert path == str(tmp_path / "images" / "art%20work.png")


@pytest.mark.parametrize("reference,expected", [
    ("images/chapter%23one.png", "images/chapter#one.png"),
    ("images/query%3Fone.png", "images/query_one.png"),
    ("images/hero.png?v=1#cover", "images/hero.png"),
])
def test_encoded_path_characters_are_not_mistaken_for_url_delimiters(reference, expected):
    assert _safe_rel_path(reference) == expected


def test_cyoap_scanner_ignores_malformed_reference_and_keeps_valid_assets():
    images, media = set(), set()
    _scan_cyoap_assets({"notes": "https://[broken", "image": "images/hero.png"}, images, media)
    assert images == {"images/hero.png"}


def test_corrupt_xlsx_import_is_contained(tmp_path):
    pytest.importorskip("pandas")
    source = tmp_path / "queue.xlsx"
    # A valid outer ZIP with a broken workbook XML reaches the Excel reader.
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("xl/workbook.xml", "<broken")
    assert batch.import_queue_items_from_file(str(source)) == []


@pytest.mark.parametrize("name", ["queue.txt", "queue.csv"])
def test_remote_csv_import_ignores_leading_blank_lines(monkeypatch, name):
    payload = b"\n\nurl,filename,mode\nhttps://example.test/game,Story,zip\n"
    response = SimpleNamespace(
        headers={}, encoding="utf-8", iter_content=lambda **kwargs: iter([payload]), close=lambda: None,
    )
    monkeypatch.setattr(batch, "fetch_response", lambda *args, **kwargs: response)
    assert batch.import_queue_items_from_source("https://example.test/" + name) == [
        {"url": "https://example.test/game", "filename": "Story", "mode": "zip"},
    ]


@pytest.mark.parametrize("reference", ["images/missing hero.png", "images/日本.png", "images/hero.png?v=1", "images/hero's.png", "images\\hero.png"])
def test_verify_json_asset_names_are_checked(tmp_path, reference):
    (tmp_path / "project.json").write_text(json.dumps({"rows": [], "image": reference}), encoding="utf-8")
    ok, report = package.verify_output_package(str(tmp_path))
    assert not ok
    assert "missing asset:" in report
    asset = tmp_path / unquote(reference.split("?", 1)[0].replace("\\", "/"))
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"fixture")
    ok, report = package.verify_output_package(str(tmp_path))
    assert ok, report


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "utf-8-sig"])
@pytest.mark.parametrize("extension", ["txt", "csv"])
def test_local_batch_import_accepts_unicode_bom(tmp_path, encoding, extension):
    source = tmp_path / ("queue." + extension)
    text = "https://example.test/game | Pilihan 日本 | zip\n" if extension == "txt" else "url,filename,mode\nhttps://example.test/game,Pilihan 日本,zip\n"
    source.write_bytes(text.encode(encoding))
    assert batch.import_queue_items_from_file(str(source)) == [
        {"url": "https://example.test/game", "filename": "Pilihan 日本", "mode": "zip"},
    ]
    if extension == "csv":
        assert batch._import_csv_without_pandas(str(source))[0]["filename"] == "Pilihan 日本"


@pytest.mark.parametrize("filename", ["Story | Extra", "Story\nhttps://other.test/", "Story\rExtra"])
def test_txt_queue_export_rejects_unrepresentable_fields_without_overwriting(tmp_path, filename):
    target = tmp_path / "queue.txt"
    target.write_text("previous export", encoding="utf-8")
    with pytest.raises(ValueError, match="CSV"):
        batch.export_queue_items_to_file([{"url": "https://example.test/", "filename": filename}], str(target))
    assert target.read_text(encoding="utf-8") == "previous export"


def test_settings_import_reports_oversized_integer(tmp_path):
    source = tmp_path / "settings.json"
    source.write_text('{"threads":' + "9" * 10000 + "}", encoding="utf-8")
    ok, message = settings.import_settings(str(source))
    assert not ok
    assert "invalid JSON" in message


@pytest.mark.parametrize("folder", ["chapter%20one", "chapter%2520one", "chapter#one", "日本"])
def test_itch_html5_preserves_literal_entry_folder(tmp_path, monkeypatch, folder):
    archive = tmp_path / "story.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr(folder + "/index.html", "<html>Story</html>")
    monkeypatch.setattr(itch_offline, "_localize_preserved_index_assets", lambda html, *args, **kwargs: html)
    entry, reused, complete = itch_offline.materialize_itch_html5_archive(archive, "https://creator.itch.io/game")
    assert Path(entry).parent.name == folder
    assert not reused and complete
    assert itch_offline.materialize_itch_html5_archive(archive, "https://creator.itch.io/game") == (entry, True, True)


def test_http_redirect_chain_stops_on_cancellation(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    log = SimpleNamespace(**{level: lambda *a, **kw: None for level in ("debug", "info", "warning", "error")})
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(logger=log, _CLOUDFLARE_MODE="off"))
    monkeypatch.setattr(fetch_base, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda url: None)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda host: False)
    monkeypatch.setattr(fetch_base, "_ssrf_block_cross_origin", lambda *args: False)
    requests, closed = [], []

    def get(url, **kwargs):
        requests.append(url)
        if len(requests) > 1:
            pytest.fail("request was sent after cancellation")
        event.set()
        return SimpleNamespace(status_code=302, headers={"Location": "/next"}, close=lambda: closed.append(True))

    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **kwargs: SimpleNamespace(get=get))
    with pytest.raises(DownloadCancelledError):
        fetch_base.base_fetch_response("https://example.test/")
    assert closed == [True]


def test_cancelled_stream_does_not_begin_reading_body(tmp_path, monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(cancellation, "_ACTIVE_CANCEL_EVENT", event)
    target = tmp_path / "image.png"
    target.write_bytes(b"previous")
    response = SimpleNamespace(headers={}, iter_content=lambda **kwargs: pytest.fail("cancelled body was read"))
    with pytest.raises(DownloadCancelledError):
        package.atomic_stream_response_to_file(response, str(target))
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]
