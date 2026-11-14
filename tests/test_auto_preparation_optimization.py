"""Auto should prepare bounded work without delaying the first queue item."""

import threading
from types import SimpleNamespace

import pytest
import requests

from cyoa_downloader_app.core import cancellation
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.gui import final_behaviors as gui
from cyoa_downloader_app.gui.auto_prepare import AutoModePreparation
from cyoa_downloader_app.project import cyoap_vue, discover
from tools.benchmark_auto_preparation import missing_site_fixture, queue_fixture


def test_missing_site_needs_at_most_two_initial_requests():
    result = missing_site_fixture()
    assert result["mode"] == "website_folder"
    assert result["requests"] <= 2


@pytest.mark.parametrize("first_manual", [False, True])
def test_first_download_does_not_wait_for_later_detection(first_manual):
    first_download = threading.Event()

    def detector(url, cancel):
        if not url.endswith("/0/"):
            assert first_download.wait(2), "later Auto blocked the first download"
        return "website_folder"

    result = queue_fixture(first_manual=first_manual, detector=detector,
                           on_download=lambda kwargs, cancel: first_download.set())
    assert first_download.is_set()
    assert not result["worker_failed"]
    assert [row["status"] for row in result["results"]] == ["OK"] * 12
    assert all(row["mode"] == "website_folder" for row in result["results"])
    assert [row["file_name"] for row in result["downloads"]] == [str(i) for i in range(12)]


def test_cancellation_during_first_download_limits_speculative_probes():
    result = queue_fixture(count=100, on_download=lambda kwargs, cancel: cancel.set())
    assert len(result["probes"]) <= 2
    assert len(result["downloads"]) == 1
    assert result["results"][0]["status"] == "CANCELLED"
    assert not result["worker_failed"]


def test_resume_and_duplicate_urls_keep_snapshot_identity():
    items = [
        {"url": "https://example.test/same/", "mode": "auto", "_run_requested_mode": "auto",
         "_queue_id": str(i), "_run_file_name": str(i), "_resume_key": f"job-v2:{i}"}
        for i in range(4)
    ]
    result = queue_fixture(items=items, completed=["job-v2:0", "job-v2:2"])
    assert len(result["probes"]) == 2
    assert [row["file_name"] for row in result["downloads"]] == ["1", "3"]
    assert [row["queue_id"] for row in result["results"]] == ["0", "1", "2", "3"]
    assert [row["status"] for row in result["results"]] == ["SKIP", "OK", "SKIP", "OK"]


def test_lookahead_overlaps_download_and_does_not_mutate_next_row():
    items = [{"url": str(i), "mode": "auto"} for i in range(100)]
    started = threading.Event()
    release = threading.Event()
    calls = []

    def detect(url):
        calls.append(url)
        if url == "1":
            started.set()
            assert release.wait(2)
        return "website_folder"

    preparation = AutoModePreparation(items, detect, lambda it: it["mode"] == "auto")
    try:
        assert preparation.resolve(0) == "website_folder"
        preparation.prepare(1)
        assert started.wait(2)
        assert items[1]["mode"] == "auto"
        assert calls == ["0", "1"]
        release.set()
        assert preparation.resolve(1) == "website_folder"
    finally:
        release.set()
        preparation.close()


def test_cancel_waits_for_active_lookahead_before_finishing():
    started = threading.Event()
    stopped = threading.Event()

    def detector(url, cancel):
        if url.endswith("/1/"):
            started.set()
            assert cancel.wait(2)
            stopped.set()
            raise DownloadCancelledError("stopped lookahead")
        return "website_folder"

    def cancel_download(kwargs, cancel):
        assert started.wait(2)
        cancel.set()

    result = queue_fixture(detector=detector, on_download=cancel_download)
    assert stopped.is_set()
    assert len(result["downloads"]) == 1
    assert len(result["probes"]) == 2
    assert result["results"][0]["status"] == "CANCELLED"


def response(payload, content_type="application/json", status=200):
    result = requests.Response()
    result.status_code = status
    result._content = payload
    result.headers["Content-Type"] = content_type
    result.encoding = "utf-8"
    return result


@pytest.mark.parametrize("bad_list", [b"{}", b"<html>SPA fallback</html>", b"broken JSON"])
def test_preparation_retains_no_partial_or_invalid_pair(monkeypatch, bad_list):
    monkeypatch.setattr(discover, "_load_settings", lambda: {})
    monkeypatch.setattr(cyoap_vue, "fetch_response", lambda url, **kw: response(
        b'{"title":"demo"}' if url.endswith("platform.json") else bad_list))
    result = discover.prepare_auto_mode("https://example.test/game/?signature=a%2Fb")
    assert result.mode == "website_folder"
    assert result.cyoap_payloads == {}
    assert result.effective_url == "https://example.test/game/?signature=a%2Fb"


def test_cyoap_reuses_validated_pair_with_identical_output_bytes(monkeypatch, tmp_path):
    base = "https://example.test/game/"
    platform = '{ "title": "日本語", "image":"images/hero.png" }\n'.encode()
    payloads = {base + "dist/platform.json": platform, base + "dist/nodes/list.json": b'["one.json"]\n',
                base + "dist/nodes/one.json": b'{"image":"images/hero.png"}',
                base + "images/hero.png": b"image-bytes", base: b"<html></html>",
                base + "favicon.ico": b"icon"}
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return response(payloads[url]) if url in payloads else response(b"", status=404)

    monkeypatch.setattr(discover, "_load_settings", lambda: {})
    monkeypatch.setattr(cyoap_vue, "fetch_response", fetch)
    detection = discover.prepare_auto_mode(base)
    assert detection.mode == "cyoap_vue_folder"
    assert detection.reason == "cyoap_structure"
    assert len(detection.cyoap_payloads) == 2
    for label, prepared in (("reused", detection.cyoap_payloads), ("control", None)):
        assert cyoap_vue.try_download_cyoap_vue_site(base, str(tmp_path / label), website_zip_output=False,
                                                   prepared_payloads=prepared)
    assert calls.count(base + "dist/platform.json") == 2  # probe + control, no reused request
    assert calls.count(base + "dist/nodes/list.json") == 2
    for rel in ("game/dist/platform.json", "game/dist/nodes/list.json", "game/dist/nodes/one.json",
                "game/images/hero.png", "game/index.html", "game/favicon.ico"):
        assert (tmp_path / "reused" / rel).read_bytes() == (tmp_path / "control" / rel).read_bytes()


def test_large_cyoap_pair_is_not_retained(monkeypatch):
    monkeypatch.setattr(cyoap_vue, "fetch_response", lambda url, **kw: response(
        b'{"title":"' + b"x" * (256 * 1024) + b'"}' if url.endswith("platform.json") else b"[]"))
    result = discover.prepare_auto_mode("https://example.test/game/")
    assert result.mode.startswith("cyoap_vue_")
    assert not result.cyoap_payloads


@pytest.mark.parametrize("wrapper,binding", [(gui._v466_run_download, "_V466_PREVIOUS_RUN_DOWNLOAD"),
                                           (gui._v462_run_download, "_V461_RUN_DOWNLOAD")])
def test_compatibility_download_wrappers_forward_prepared_pair(monkeypatch, wrapper, binding):
    calls = []
    monkeypatch.setattr(gui, binding, lambda **kwargs: calls.append(kwargs))
    pair = {"https://example.test/game/dist/platform.json": "{}"}
    wrapper("https://example.test/game/", prepared_cyoap=pair)
    assert calls[0]["prepared_cyoap"] is pair


def test_gui_download_receives_its_own_prepared_pair():
    def detect(url, cancel):
        return discover.AutoDetection("cyoap_vue_folder", url, "cyoap_structure", {url: "{}"})

    def download(kwargs, cancel):
        assert kwargs["prepared_cyoap"] == {kwargs["url"]: "{}"}

    result = queue_fixture(detector=detect, on_download=download)
    assert [row["status"] for row in result["results"]] == ["OK"] * 12


def test_static_catalogue_avoids_structure_probes(monkeypatch):
    from cyoa_downloader_app.project import cyoa_cafe

    monkeypatch.setattr(cyoa_cafe, "fetch_cyoa_cafe_record", lambda url: {})
    monkeypatch.setattr(cyoa_cafe, "classify_cyoa_cafe_record", lambda record: "static_pages")
    monkeypatch.setattr(cyoap_vue, "fetch_response", lambda *a, **kw: pytest.fail("unneeded probe"))
    result = discover.prepare_auto_mode("https://cyoa.cafe/game/static")
    assert result.reason == "static_catalogue"
    assert result.mode.startswith("website_")


def test_catalogue_iframe_preserves_effective_signed_url(monkeypatch):
    signed = "https://example.test/game/?signature=a%2Fb"
    monkeypatch.setattr(discover, "_legacy", lambda: SimpleNamespace(get_iframe_url_from_cyoa_cafe=lambda url: signed))
    monkeypatch.setattr(cyoap_vue, "fetch_response", lambda url, **kw: response(b"", status=404))
    result = discover.prepare_auto_mode("https://creator.cyoa.cafe/story/")
    assert result.effective_url == signed


def test_gui_preparation_preserves_custom_catalogue_resolution_kind(monkeypatch):
    url = "https://cyoa.cafe/game/custom"
    monkeypatch.setattr(gui, "prepare_auto_mode", lambda url: discover.AutoDetection(
        "website_folder", "https://example.test/custom/", "website_fallback"))
    monkeypatch.setattr(gui, "_v462_get_resolution_kind", lambda url: "pure_website")
    monkeypatch.setattr(gui, "_load_settings", lambda: {"auto_detect_output": "folder"})
    detection = gui._v46_prepare_auto_mode(url)
    assert detection.mode == "pure_website_folder"
    assert detection.effective_url == "https://example.test/custom/"


def test_preparation_cancellation_does_not_return_fallback(monkeypatch):
    def cancelled(*args, **kwargs):
        raise DownloadCancelledError("probe cancelled")

    monkeypatch.setattr(cyoap_vue, "fetch_response", cancelled)
    with pytest.raises(DownloadCancelledError):
        discover.prepare_auto_mode("https://example.test/game/")
    assert not cancellation._cancel_requested()
