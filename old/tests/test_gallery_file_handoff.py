"""Extractor files stay file-backed through media storage and embedding."""

import base64
import builtins
import errno
import hashlib
import json
import threading
from pathlib import Path

import pytest

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.media_io import MEDIA_CHUNK_BYTES, FilePayload
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.download import image_pipeline as pipeline
from cyoa_downloader_app.integrations import gallery_dl


@pytest.fixture
def extractor(monkeypatch):
    body = b"\x89PNG\r\n\x1a\n" + b"gallery fixture" * 20_000
    scratch = []
    def run(cmd):
        directory = Path(cmd[cmd.index("--destination") + 1])
        scratch.append(directory)
        (directory / "nested").mkdir()
        (directory / "nested" / "largest.png").write_bytes(body)
        (directory / "small.png").write_bytes(body[:100])
        (directory / "partial.png.part").write_bytes(body * 2)
        (directory / "metadata.json").write_bytes(body * 2)
        return 1, "partial job with one complete image"
    for name, value in (
        ("_is_gallery_dl_candidate", lambda *a: "fixture"), ("_gallery_dl_is_available", lambda: True),
        ("_run_gallery_dl", run), ("_get_active_proxy", lambda: ""), ("_gallery_dl_config", ""),
    ):
        monkeypatch.setattr(gallery_dl, name, value)
    return body, scratch


def test_legacy_byte_api_and_largest_complete_file_selection(extractor):
    body, scratch = extractor
    assert gallery_dl._fetch_via_gallery_dl("https://example.test/posts/1.png") == body
    assert scratch and all(not path.exists() for path in scratch)


def test_file_handoff_reads_bounded_chunks_and_outlives_extractor_scratch(extractor, monkeypatch, tmp_path):
    body, scratch = extractor
    original_open = builtins.open
    reads = []
    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def read(self, size=-1):
            assert 0 < size <= MEDIA_CHUNK_BYTES
            reads.append(size)
            return self.handle.read(size)
    def open_file(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return Reader(handle) if str(path).endswith("largest.png") and mode == "rb" else handle
    monkeypatch.setattr(builtins, "open", open_file)
    payload = gallery_dl._fetch_via_gallery_dl("https://example.test/posts/1.png", spool_dir=str(tmp_path))
    assert isinstance(payload, FilePayload) and reads
    assert len(payload) == len(body) and payload.prefix == body[:512]
    assert payload.sha256 == hashlib.sha256(body).hexdigest()
    assert Path(payload.path).read_bytes() == body
    assert Path(payload.path).parent == tmp_path and all(not path.exists() for path in scratch)
    assert not list(tmp_path.glob("*.part"))


def test_disk_failure_propagates_and_backend_scratch_is_cleaned(extractor, monkeypatch, tmp_path):
    _, scratch = extractor
    def fail(*a, **kw):
        raise OSError(errno.ENOSPC, "fixture disk full")
    monkeypatch.setattr(gallery_dl, "spool_chunks", fail)
    with pytest.raises(OSError, match="fixture disk full"):
        gallery_dl._fetch_via_gallery_dl("https://example.test/posts/1.png", spool_dir=str(tmp_path))
    assert all(not path.exists() for path in scratch) and not list(tmp_path.iterdir())


def test_cancel_during_file_read_removes_part_and_backend_scratch(extractor, monkeypatch, tmp_path):
    _, scratch = extractor
    cancel = threading.Event()
    cancellation.set_progress_event_sink(None, cancel)
    original_open = builtins.open
    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def read(self, size):
            block = self.handle.read(size)
            cancel.set()
            return block
    def open_file(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return Reader(handle) if str(path).endswith("largest.png") and mode == "rb" else handle
    monkeypatch.setattr(builtins, "open", open_file)
    try:
        with pytest.raises(DownloadCancelledError):
            gallery_dl._fetch_via_gallery_dl("https://example.test/posts/1.png", spool_dir=str(tmp_path))
    finally:
        cancellation.clear_progress_event_sink()
    assert all(not path.exists() for path in scratch) and not list(tmp_path.iterdir())


@pytest.fixture
def media(monkeypatch, extractor):
    retained = []
    def cache_file(url, payload):
        assert isinstance(payload, FilePayload)
        retained.append(payload)
    for name, value in (
        ("_cache_get_file", lambda *a: None), ("_cache_put_file", cache_file),
        ("_is_gallery_dl_site", lambda *a: "fixture"),
        ("_fetch_via_gallery_dl", gallery_dl._fetch_via_gallery_dl),
        ("_ssrf_block_cross_origin", lambda *a: False),
        ("_fetch_headless", lambda *a, **kw: pytest.fail("Unexpected browser fallback")),
        ("fetch_response", lambda *a, **kw: pytest.fail("Unexpected direct request")),
    ):
        monkeypatch.setattr(pipeline, name, value)
    return retained


@pytest.mark.parametrize("mode", ["folder", "embed", "both"])
def test_pipeline_native_file_handoff_in_all_media_modes(media, extractor, tmp_path, mode):
    body, scratch = extractor
    target = tmp_path / "embedded.json"
    def writer(chunks):
        with target.open("w", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(chunk)
    _, localized, resolved = pipeline.process_images(json.dumps({"image": "https://example.test/posts/1.png"}),
        "https://example.test/", embed=mode != "folder", download=mode != "embed",
        temp_folder=str(tmp_path), max_workers=2, embed_writer=writer if mode != "folder" else None)
    assert resolved and media and all(not Path(payload.path).exists() for payload in media)
    if mode != "embed":
        assert (tmp_path / json.loads(localized)["image"]).read_bytes() == body
    if mode != "folder":
        embedded = json.loads(target.read_text("utf-8"))["image"]
        assert base64.b64decode(embedded.split(",", 1)[1]) == body
    assert all(not path.exists() for path in scratch)
    assert not list(tmp_path.rglob("*.part")) and not list(tmp_path.glob(".cyoa-media-*"))


def test_pipeline_does_not_launch_another_backend_for_job_disk_failure(media, extractor, monkeypatch, tmp_path):
    def fail(*a, **kw):
        raise OSError(errno.ENOSPC, "job spool full")
    monkeypatch.setattr(gallery_dl, "spool_chunks", fail)
    with pytest.raises(OSError, match="job spool full"):
        pipeline.process_images(json.dumps({"image": "https://example.test/posts/1.png"}), "https://example.test/",
            download=True, temp_folder=str(tmp_path), max_workers=1)
    assert not list(tmp_path.glob(".cyoa-media-*"))


def test_legacy_plugin_callable_keeps_one_argument_signature(media, monkeypatch, tmp_path):
    body = b"\x89PNG\r\n\x1a\n" + b"legacy" * 100
    calls = []
    def legacy(url):
        calls.append(url)
        return body
    monkeypatch.setattr(pipeline, "_fetch_via_gallery_dl", legacy)
    _, localized, resolved = pipeline.process_images(json.dumps({"image": "a.png"}), "https://example.test/",
        download=True, temp_folder=str(tmp_path), max_workers=1)
    assert calls == ["https://example.test/a.png"] and resolved
    assert (tmp_path / json.loads(localized)["image"]).read_bytes() == body


def test_gallery_file_handoff_after_exhausted_direct_transport(media, extractor, monkeypatch, tmp_path):
    body, _ = extractor
    candidates = iter(("", "fixture"))
    monkeypatch.setattr(pipeline, "_is_gallery_dl_site", lambda *a: next(candidates))
    monkeypatch.setattr(pipeline, "fetch_response", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "_domain_record_failure", lambda *a: 0)
    _, localized, resolved = pipeline.process_images(json.dumps({"image": "a.png"}), "https://example.test/",
        download=True, temp_folder=str(tmp_path), max_workers=1)
    assert resolved and media and (tmp_path / json.loads(localized)["image"]).read_bytes() == body


def test_unavailable_output_still_returns_none_and_cleans_backend_scratch(extractor, monkeypatch, tmp_path):
    directories = []
    def empty(cmd):
        directories.append(Path(cmd[cmd.index("--destination") + 1]))
        return 1, "nothing extracted"
    monkeypatch.setattr(gallery_dl, "_run_gallery_dl", empty)
    assert gallery_dl._fetch_via_gallery_dl("https://example.test/posts/1.png", spool_dir=str(tmp_path)) is None
    assert directories and all(not directory.exists() for directory in directories)
    assert not list(tmp_path.iterdir())
