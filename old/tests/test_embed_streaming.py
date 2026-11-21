"""Embedded JSON output must preserve content without retaining whole media."""

import base64
import errno
import hashlib
import json
import threading
from pathlib import Path

import pytest
import requests

from cyoa_downloader_app.core import cancellation, media_io
from cyoa_downloader_app.core.embed_io import EmbeddedMedia, project_chunks, value_chunks
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import image_pipeline as pipeline
from cyoa_downloader_app.download.package import save_text_chunks_to_file


@pytest.fixture
def media(monkeypatch):
    calls = []
    body = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 1000
    for name in ("_cache_get", "_cache_get_file"):
        monkeypatch.setattr(pipeline, name, lambda *a, **kw: None)
    for name in ("_cache_put", "_cache_put_file", "_domain_record_success", "_throttle_bandwidth"):
        monkeypatch.setattr(pipeline, name, lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(pipeline, "_ssrf_block_cross_origin", lambda *a: False)
    def fetch(url, **kwargs):
        assert kwargs["stream"] and not kwargs["as_bytes"]
        calls.append(url)
        result = requests.Response()
        result.url, result.status_code = url, 200
        result.headers.update({"Content-Type": "audio/mpeg" if ".mp3" in url else "image/png"})
        result._content = body
        result._content_consumed = True
        return result
    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    return body, calls


@pytest.mark.parametrize("size", [0, 1, 2, 3, 49151, 49152, 49153, 200003])
def test_base64_stream_padding_and_chunk_bound(tmp_path, size):
    body = (bytes(range(256)) * ((size + 255) // 256))[:size]
    path = tmp_path / "media"
    path.write_bytes(body)
    chunks = list(value_chunks(EmbeddedMedia(str(path), "image/png")))
    assert "".join(chunks) == "data:image/png;base64," + base64.b64encode(body).decode()
    assert max(map(len, chunks)) <= media_io.MEDIA_CHUNK_BYTES


@pytest.mark.parametrize("both", [False, True])
def test_file_sink_matches_string_api_for_alias_escaped_url_credits_and_unicode(media, tmp_path, both):
    body, calls = media
    project = json.dumps({"title": "α 🎲", "rows": [
        {"image": "image.png", "backgroundImage": "./image.png", "audio": "song.mp3"},
        {"image": "https://example.test/other.png Credit Artist", "bgmId": "song.mp3", "useAudioURL": True},
        {"text": '<img src="https://example.test/inline.png">', "image": "data:image/png;base64,YQ=="},
    ]}, ensure_ascii=False).replace('"./image.png"', '".\\/image.png"')
    standard = pipeline.process_images(project, "https://example.test/", embed=True, download=both,
                                       temp_folder=str(tmp_path / "standard"), embed_audio=True, max_workers=1)
    calls.clear()
    output = tmp_path / "streamed.json"
    def writer(chunks):
        media_io.atomic_write_text_chunks(output, chunks)
    streamed = pipeline.process_images(project, "https://example.test/", embed=True, download=both,
                                       temp_folder=str(tmp_path / "streamed"), embed_audio=True,
                                       max_workers=1, embed_writer=writer)
    assert streamed == ("", standard[1], standard[2])
    assert output.read_text("utf-8") == standard[0]
    parsed = json.loads(standard[0])
    assert parsed["title"] == "α 🎲"
    for row in parsed["rows"][:2]:
        assert base64.b64decode(row["image"].split(",", 1)[1]) == body
    assert parsed["rows"][2]["image"] == "data:image/png;base64,YQ=="
    assert len(calls) == 4  # Relative image aliases share one fetch.
    assert not list(tmp_path.rglob(".cyoa-media-*"))
    if both:
        for path in (tmp_path / "standard").rglob("*"):
            if path.is_file():
                assert path.read_bytes() == (tmp_path / "streamed" / path.relative_to(tmp_path / "standard")).read_bytes()


def test_no_external_assets_sink_preserves_original_bytes(tmp_path):
    source = '{  "name": "雪", "image": "data:image/png;base64,YQ==" }'
    output = tmp_path / "out.json"
    result = pipeline.process_images(source, "https://example.test/", embed=True,
                                     embed_writer=lambda chunks: media_io.atomic_write_text_chunks(output, chunks))
    assert result == ("", source, set())
    assert output.read_bytes() == source.encode()


def test_original_data_uri_and_replacement_are_never_rescanned(tmp_path):
    file = tmp_path / "payload"
    file.write_bytes(b"abc")
    text = '{"image": "AAA", "backgroundImage": "data:image/png;base64,AAAA", "text":"AAA"}'
    result = "".join(project_chunks(text, {"AAA": EmbeddedMedia(str(file), "image/png")},
                                   "image|backgroundImage", {"image", "backgroundimage"}, {}))
    assert json.loads(result)["backgroundImage"] == "data:image/png;base64,AAAA"
    assert json.loads(result)["image"] == "data:image/png;base64,YWJj"


def test_cancel_atomic_json_write_preserves_previous_file_and_removes_partial(tmp_path):
    target = tmp_path / "project.json"
    target.write_bytes(b"old")
    cancel = threading.Event()
    def chunks():
        yield "first"
        cancel.set()
        yield "second"
    cancellation.set_progress_event_sink(None, cancel)
    try:
        with pytest.raises(DownloadCancelledError):
            media_io.atomic_write_text_chunks(target, chunks())
        assert target.read_bytes() == b"old" and not list(tmp_path.glob("*.part"))
    finally:
        cancellation.clear_progress_event_sink()


def test_sink_disk_full_joins_producers_and_removes_run_files(media, tmp_path):
    def writer(chunks):
        for _chunk in chunks:
            raise OSError(errno.ENOSPC, "fixture disk full")
    with pytest.raises(OSError, match="fixture disk full"):
        pipeline.process_images('{"image":"one.png"}', "https://example.test/", embed=True,
                                temp_folder=str(tmp_path), embed_writer=writer)
    assert not list(tmp_path.rglob(".cyoa-media-*"))
    assert not list(tmp_path.rglob("*.part"))


def test_atomic_writer_fsync_failure_keeps_old_output(tmp_path, monkeypatch):
    output = tmp_path / "project.json"
    output.write_bytes(b"old")
    def fail(*_):
        raise OSError(errno.ENOSPC, "fixture flush failure")
    monkeypatch.setattr(media_io.os, "fsync", fail)
    with pytest.raises(OSError, match="fixture flush failure"):
        media_io.atomic_write_text_chunks(output, iter(("new", "雪")))
    assert output.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_chunk_writer_preserves_non_overwriting_names_and_utf8(tmp_path):
    (tmp_path / "project.json").write_bytes(b"keep")
    result = save_text_chunks_to_file(iter(("雪", "🎲")), "project.json", str(tmp_path))
    assert Path(result).name == "project_1.json"
    assert Path(result).read_bytes() == "雪🎲".encode()
    assert (tmp_path / "project.json").read_bytes() == b"keep"


def test_large_audio_stays_external_while_both_saves_identical_file(media, monkeypatch, tmp_path):
    body = b"ID3" + b"x" * (10 * 1024 * 1024)
    def fetch(url, **kwargs):
        response = requests.Response()
        response.status_code, response.url = 200, url
        response.headers["Content-Type"] = "audio/mpeg"
        response._content, response._content_consumed = body, True
        return response
    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    embedded, downloaded, _ = pipeline.process_images('{"audio":"large.mp3"}', "https://example.test/",
                                                      embed=True, embed_audio=True, download=True,
                                                      temp_folder=str(tmp_path))
    assert json.loads(embedded)["audio"] == "large.mp3"
    saved = tmp_path / json.loads(downloaded)["audio"]
    assert hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(body).digest()
