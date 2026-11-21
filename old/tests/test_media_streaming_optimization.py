"""File-backed media, integrity, cache eviction, and interrupted writes."""

import errno
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from cyoa_downloader_app.core import cancellation, media_io
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import image_pipeline as pipeline
from cyoa_downloader_app.storage import cache


@pytest.fixture
def media_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "_CACHE_IDX", tmp_path / "cache" / "index.json")
    for name, value in (
        ("_cache_index", {}), ("_cache_dirty", {}), ("_cache_removed", set()),
        ("_cache_loaded", True), ("_cache_replace_generation", 0),
        ("_cache_flushed_replace_generation", 0), ("_cache_write_generation", 0),
        ("_cache_enforced_generation", -1), ("_cache_enforced_scope", None),
    ):
        monkeypatch.setattr(cache, name, value)
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 2048)
    monkeypatch.setattr(cache, "_v465_schedule_cache_save", lambda: None)
    monkeypatch.setattr(pipeline, "vpn_requirement_satisfied", lambda: True)
    monkeypatch.setattr(pipeline, "_ssrf_block_cross_origin", lambda *a: False)
    monkeypatch.setattr(pipeline, "_is_gallery_dl_site", lambda url: "")
    monkeypatch.setattr(pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(pipeline, "_domain_record_success", lambda *a: None)
    monkeypatch.setattr(pipeline, "_domain_record_failure", lambda *a: 0)
    monkeypatch.setattr(pipeline, "_throttle_bandwidth", lambda *a: None)
    monkeypatch.setattr(pipeline, "_cancel_aware_sleep", lambda *a: None)
    monkeypatch.setattr(pipeline, "_make_cookie_session", lambda *a: None)


class StreamingResponse:
    def __init__(self, body, *, mime="image/png", length=None, headers=None, status=200, chunks=None):
        self.body = body
        self.headers = {"Content-Type": mime, **(headers or {})}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.status_code = status
        self.closed = False
        self.chunks = chunks

    @property
    def content(self):
        pytest.fail("streaming path materialized response.content")

    def iter_content(self, chunk_size):
        if self.chunks is not None:
            yield from self.chunks()
        else:
            for offset in range(0, len(self.body), chunk_size):
                yield self.body[offset:offset + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def close(self):
        self.closed = True


def localize(tmp_path, project, **kwargs):
    return pipeline.process_images(json.dumps(project), "https://example.test/", download=True,
                                   temp_folder=str(tmp_path), max_workers=2, **kwargs)


@pytest.mark.parametrize("status", [429, 503])
def test_error_body_closes_before_backoff_needs_another_shared_slot(media_environment, monkeypatch, tmp_path, status):
    error = StreamingResponse(b"", status=status)
    body = b"\x89PNG\r\n\x1a\n" + b"fixture" * 100
    response = StreamingResponse(body, length=len(body))
    responses = iter((error, response))
    sleeps = []
    monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: next(responses))
    def sleep(delay):
        assert error.closed, "Backoff retained a transport lease"
        sleeps.append(delay)
    monkeypatch.setattr(pipeline, "_cancel_aware_sleep", sleep)
    _, downloaded, _ = localize(tmp_path, {"rows": [{"image": "image.png"}]})
    assert downloaded and len(sleeps) == 1 and response.closed


def test_folder_and_cache_hits_never_read_whole_bodies(media_environment, monkeypatch, tmp_path):
    body = b"\x89PNG\r\n\x1a\n" + b"x" * 200_000
    responses = []

    def fetch(url, **kwargs):
        assert kwargs["stream"] and not kwargs["as_bytes"]
        response = StreamingResponse(body, length=len(body))
        responses.append(response)
        return response

    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    project = {"rows": [{"image": "one.png"}, {"image": "two.png"}, {"image": "./one.png"}]}
    with patch.object(Path, "read_bytes", side_effect=AssertionError("whole file read")):
        _, first, resolved = localize(tmp_path / "first", project)
        assert len(responses) == 2 and all(response.closed for response in responses)
        monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: pytest.fail("cache miss"))
        _, second, _ = localize(tmp_path / "second", project)
    assert len(resolved) >= 2
    for root, localized in ((tmp_path / "first", first), (tmp_path / "second", second)):
        paths = [row["image"] for row in json.loads(localized)["rows"]]
        assert len(set(paths)) == 1
        assert (root / paths[0]).read_bytes() == body
        assert not list(root.glob(".cyoa-media-*"))


def test_audio_aliases_can_copy_from_first_committed_file(media_environment, monkeypatch, tmp_path):
    body = b"ID3" + b"x" * 1000
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return StreamingResponse(body, mime="audio/mpeg")

    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    _, localized, _ = localize(tmp_path, {"rows": [{"audio": "a.mp3?v=1"}, {"audio": "a.mp3?v=2"}]})
    assert len(calls) == 1
    paths = [row["audio"] for row in json.loads(localized)["rows"]]
    assert len(set(paths)) == 2
    assert all((tmp_path / path).read_bytes() == body for path in paths)


@pytest.mark.parametrize("length", [1, 1000])
def test_incorrect_length_is_retried_and_never_cached(media_environment, monkeypatch, tmp_path, length):
    responses = []

    def fetch(url, **kwargs):
        response = StreamingResponse(b"ID3" + b"x" * 100, mime="audio/mpeg", length=length)
        responses.append(response)
        return response

    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    _, localized, resolved = localize(tmp_path, {"audio": "bad.mp3"})
    assert json.loads(localized)["audio"] == "bad.mp3"
    assert not resolved and not cache._cache_index
    assert len(responses) == 4 and all(response.closed for response in responses)
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.asset"))


def test_compressed_wire_length_is_not_decoded_length(media_environment, monkeypatch, tmp_path):
    body = b"ID3" + b"x" * 1000
    monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: StreamingResponse(
        body, mime="audio/mpeg", length=10, headers={"Content-Encoding": "gzip"}))
    _, localized, _ = localize(tmp_path, {"audio": "song.mp3"})
    assert (tmp_path / json.loads(localized)["audio"]).read_bytes() == body


def test_mislabeled_html_split_across_chunks_is_rejected(media_environment, monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: StreamingResponse(
        b"", chunks=lambda: iter([b" ", b"<!doc", b"type html><html>error</html>"])))
    _, localized, resolved = localize(tmp_path, {"image": "bad.png"})
    assert json.loads(localized)["image"] == "bad.png"
    assert not resolved and not cache._cache_index
    assert not list(tmp_path.glob("images/*"))


def test_cancellation_joins_stream_workers_and_removes_partials(media_environment, monkeypatch, tmp_path):
    event = threading.Event()
    responses = []

    def chunks():
        yield b"ID3" + b"x" * 100
        event.set()
        yield b"tail"

    def fetch(*args, **kwargs):
        response = StreamingResponse(b"", mime="audio/mpeg", chunks=chunks)
        responses.append(response)
        return response

    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    cancellation.set_progress_event_sink(None, event)
    try:
        with pytest.raises(DownloadCancelledError):
            localize(tmp_path, {"rows": [{"audio": "one.mp3"}, {"audio": "two.mp3"}]})
        assert responses and all(response.closed for response in responses)
        assert not list(tmp_path.glob(".cyoa-media-*"))
        assert not list(tmp_path.rglob("*.part"))
        assert not cache._cache_index
    finally:
        cancellation.clear_progress_event_sink()


def test_disk_full_keeps_previous_destination_and_removes_partials(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    monkeypatch.setattr(media_io.os, "fsync", lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    with pytest.raises(OSError):
        media_io.atomic_copy_file(source, target)
    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_size_limit_cleans_up_spool(tmp_path):
    with pytest.raises(media_io.ResponseBodySizeError):
        media_io.spool_chunks([b"x" * 10, b"tail"], tmp_path, max_bytes=10)
    assert not list(tmp_path.iterdir())


def test_cache_clear_does_not_remove_run_owned_payload(media_environment, tmp_path):
    body = b"x" * 1000
    cache._cache_put("url", body)
    hit = cache._cache_get_file("url", str(tmp_path))
    assert hit is not None
    cache._clear_image_cache()
    assert Path(hit.path).read_bytes() == body
    assert cache._cache_get_file("url", str(tmp_path)) is None


def test_corrupt_cache_is_rejected_and_file_put_repairs_it(media_environment, tmp_path):
    body = b"x" * 1000
    payload = media_io.spool_chunks([body], tmp_path)
    cache._cache_put_file("url", payload)
    stored = cache._CACHE_DIR / payload.sha256[:2] / payload.sha256
    stored.write_bytes(b"corrupt")
    assert cache._cache_get_file("url", str(tmp_path)) is None
    assert list(tmp_path.glob("*.asset")) == [Path(payload.path)]
    cache._cache_put_file("url", payload)
    assert stored.read_bytes() == body
    assert cache._cache_index["url"] == hashlib.sha256(body).hexdigest()


def test_file_cache_honors_capacity_and_streamed_hash(media_environment, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 1)
    first = media_io.spool_chunks([b"a" * 700_000], tmp_path)
    second = media_io.spool_chunks([b"b" * 700_000], tmp_path)
    cache._cache_put_file("first", first)
    cache._cache_put_file("second", second)
    assert cache._cache_get_file("first", str(tmp_path)) is None
    hit = cache._cache_get_file("second", str(tmp_path))
    assert hit.sha256 == second.sha256 and hit.size == second.size


def test_partial_transport_failure_is_retried(media_environment, monkeypatch, tmp_path):
    calls = []

    def interrupted():
        yield b"partial"
        raise requests.exceptions.ChunkedEncodingError("interrupted")

    def fetch(url, **kwargs):
        calls.append(url)
        return StreamingResponse(b"ID3" + b"x" * 100, mime="audio/mpeg",
                                 chunks=interrupted if len(calls) == 1 else None)

    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    _, localized, _ = localize(tmp_path, {"audio": "a.mp3"})
    assert len(calls) == 2
    assert (tmp_path / json.loads(localized)["audio"]).read_bytes() == b"ID3" + b"x" * 100
    assert not list(tmp_path.rglob("*.part"))


def test_identity_length_validation_for_spools(tmp_path):
    response = SimpleNamespace(headers={"Content-Length": "3"})
    payload = media_io.spool_chunks([b"a", b"bc"], tmp_path, response=response)
    assert payload.prefix == b"abc" and payload.size == 3


def test_streamed_files_and_json_match_buffered_output(media_environment, monkeypatch, tmp_path):
    bodies = {"https://example.test/one.png": b"\x89PNG\r\n\x1a\n" + b"a" * 1000,
              "https://example.test/two.png": b"\x89PNG\r\n\x1a\n" + b"b" * 1000,
              "https://example.test/song.mp3": b"ID3" + b"c" * 1000}

    def fetch(url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = bodies[url]
        response._content_consumed = True
        response.headers["Content-Type"] = "audio/mpeg" if url.endswith(".mp3") else "image/png"
        return response

    monkeypatch.setattr(pipeline, "fetch_response", fetch)
    monkeypatch.setattr(pipeline, "_cache_get", lambda *a: None)
    monkeypatch.setattr(pipeline, "_cache_get_file", lambda *a: None)
    project = json.dumps({"rows": [{"image": "one.png"}, {"image": "two.png"},
                                    {"image": "./one.png", "audio": "song.mp3"}]})
    control = pipeline._process_images(project, "https://example.test/", download=True,
                                       temp_folder=str(tmp_path / "control"))
    streamed = pipeline.process_images(project, "https://example.test/", download=True,
                                        temp_folder=str(tmp_path / "streamed"))
    assert streamed == control
    for path in (tmp_path / "control").rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (tmp_path / "streamed" / path.relative_to(tmp_path / "control")).read_bytes()


def test_resume_after_cancel_uses_new_run_owned_files(media_environment, monkeypatch, tmp_path):
    event = threading.Event()

    def cancel_chunks():
        event.set()
        yield b"partial"

    monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: StreamingResponse(b"", chunks=cancel_chunks))
    cancellation.set_progress_event_sink(None, event)
    try:
        with pytest.raises(DownloadCancelledError):
            localize(tmp_path, {"image": "one.png"})
        event.clear()
        body = b"\x89PNG\r\n\x1a\n" + b"x" * 1000
        monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: StreamingResponse(body))
        _, localized, _ = localize(tmp_path, {"image": "one.png"})
        assert (tmp_path / json.loads(localized)["image"]).read_bytes() == body
        assert not list(tmp_path.glob(".cyoa-media-*"))
    finally:
        cancellation.clear_progress_event_sink()


def test_cache_copy_failure_does_not_invalidate_valid_source(media_environment, monkeypatch, tmp_path):
    cache._cache_put("url", b"x" * 1000)
    digest = cache._cache_index["url"]
    monkeypatch.setattr(cache, "spool_chunks", lambda *a, **kw: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    assert cache._cache_get_file("url", str(tmp_path)) is None
    assert cache._cache_index["url"] == digest
