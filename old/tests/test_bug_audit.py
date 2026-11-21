"""Regression cases from the full-program bug inspection (offline fixtures)."""

import io
import json
import stat
import threading
import warnings
import zipfile
from types import SimpleNamespace

import pytest
import requests

from cyoa_downloader_app.config import settings
from cyoa_downloader_app.core.archive import validate_zip_archive
from cyoa_downloader_app.core.paths import _safe_archive_rel_path
from cyoa_downloader_app.diagnostics import runtime as diagnostics
from cyoa_downloader_app.download import package
from cyoa_downloader_app.download.archive_policy import ArchivePolicy
from cyoa_downloader_app.download.route_crawler import RouteCrawler
from cyoa_downloader_app.importers import batch
from cyoa_downloader_app.network import browser, cloudflare, proxy, sessions
from cyoa_downloader_app.project.parse import normalize_project_payload_text, try_decode_bytes
from cyoa_downloader_app.runtime import state
from cyoa_downloader_app.storage import cache


def test_classic_strategy_survives_save_and_unrelated_update(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert settings._save_settings({"archive_strategy": "classic"})
    assert settings._load_settings()["archive_strategy"] == "classic"
    assert settings._update_settings({"language": "id"})
    assert settings._load_settings()["archive_strategy"] == "classic"


@pytest.mark.parametrize("url", ["http://example.test/", "https://example.test/"])
def test_environment_no_proxy_is_honored(url, monkeypatch):
    monkeypatch.setattr(state, "_proxy_mode", "inherit_env")
    monkeypatch.setattr(state, "_active_proxy", None)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080")
    monkeypatch.setenv("NO_PROXY", "example.test")
    with sessions.create_retry_session() as session:
        merged = session.merge_environment_settings(url, {}, False, True, None)
        assert not merged["proxies"]


def test_environment_https_proxy_is_not_applied_to_http(monkeypatch):
    monkeypatch.setattr(state, "_proxy_mode", "inherit_env")
    monkeypatch.setattr(state, "_active_proxy", None)
    for key in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080")
    with sessions.create_retry_session() as session:
        merged = session.merge_environment_settings("http://example.test/", {}, False, True, None)
        assert "http" not in merged["proxies"]


def test_driver_diagnostic_only_reads_local_cache(tmp_path, monkeypatch):
    manager = pytest.importorskip("selenium.webdriver.common.selenium_manager")
    driver = tmp_path / "driver.exe"
    driver.write_bytes(b"fixture")
    calls = []

    def binary_paths(self, args):
        calls.append(args)
        return {"driver_path": str(driver)}

    monkeypatch.setattr(manager.SeleniumManager, "binary_paths", binary_paths)
    assert diagnostics._selenium_manager_driver() == str(driver)
    assert "--offline" in calls[0]
    assert "--avoid-stats" in calls[0]


def test_missing_selenium_manager_is_recoverable(monkeypatch):
    manager = pytest.importorskip("selenium.webdriver.common.selenium_manager")
    from selenium.common.exceptions import WebDriverException

    def unavailable(self, args):
        raise WebDriverException("driver unavailable")

    monkeypatch.setattr(manager.SeleniumManager, "binary_paths", unavailable)
    assert diagnostics._selenium_manager_driver() == ""


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "utf-8-sig"])
def test_bom_project_payload_decodes_without_corruption(encoding):
    text = json.dumps({"rows": [], "title": "Pilihan 日本"}, ensure_ascii=False)
    decoded = try_decode_bytes(text.encode(encoding))
    assert decoded == text
    assert json.loads(normalize_project_payload_text(decoded))["title"] == "Pilihan 日本"


class BatchResponse:
    encoding = "utf-8"

    def __init__(self, payload, headers=None, error=None):
        self.payload = payload
        self.headers = headers or {}
        self.error = error
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.payload
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


def test_truncated_remote_batch_is_not_accepted(monkeypatch):
    payload = b"https://example.test/game\n"
    response = BatchResponse(payload, {"Content-Length": str(len(payload) + 10)})
    monkeypatch.setattr(batch, "fetch_response", lambda *args, **kwargs: response)
    assert batch.import_queue_items_from_source("https://example.test/queue.txt") == []
    assert response.closed


@pytest.mark.parametrize("error", [requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError])
def test_remote_batch_stream_errors_are_contained(monkeypatch, error):
    response = BatchResponse(b"https://example.test/game\n", error=error("broken stream"))
    monkeypatch.setattr(batch, "fetch_response", lambda *args, **kwargs: response)
    assert batch.import_queue_items_from_source("https://example.test/queue.txt") == []
    assert response.closed


def test_compressed_remote_batch_compares_decoded_size_only():
    payload = b"https://example.test/game\n"
    response = BatchResponse(payload, {"Content-Length": "1", "Content-Encoding": "gzip"})
    assert batch._read_remote_batch_text(response) == payload.decode()


@pytest.mark.parametrize("member", ["folder./index.html", "../bad", ".../bad", "file.\x01", "CON .png", "COM¹.png"])
def test_archive_rejects_windows_path_aliases_and_controls(member):
    with pytest.raises(ValueError):
        _safe_archive_rel_path(member)


@pytest.mark.parametrize("names", [("index.html", "index.html"), ("index.html", "INDEX.HTML")])
def test_zip_rejects_colliding_members_before_extraction(names):
    blob = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(blob, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name in names:
            archive.writestr(name, "fixture")
    with pytest.raises(ValueError, match="duplicate paths"):
        validate_zip_archive(blob.getvalue())


def test_zip_rejects_symbolic_link_members():
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        member = zipfile.ZipInfo("linked")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "../outside")
    with pytest.raises(ValueError, match="symbolic link"):
        validate_zip_archive(blob.getvalue())


@pytest.mark.parametrize("operation", ["_clear_image_cache", "_enforce_cache_limit"])
def test_cache_cleanup_does_not_traverse_junctions(tmp_path, monkeypatch, operation):
    root = tmp_path / "cache"
    folder = root / "ab"
    folder.mkdir(parents=True)
    sentinel = folder / ("ab" + "0" * 62)
    sentinel.write_bytes(b"x" * (2 * 1024 * 1024))
    monkeypatch.setattr(cache, "_CACHE_DIR", root)
    monkeypatch.setattr(cache, "_CACHE_IDX", root / "index.json")
    monkeypatch.setattr(cache, "_cache_limit_mb", lambda: 1)
    monkeypatch.setattr(cache, "_is_link_or_junction", lambda path: str(path) == str(folder), raising=False)
    monkeypatch.setattr(cache, "_v465_flush_cache_index", lambda: None)
    monkeypatch.setattr(cache, "_v465_schedule_cache_save", lambda: None)
    monkeypatch.setattr(cache, "_cache_index", {})
    monkeypatch.setattr(cache, "_cache_dirty", {})
    monkeypatch.setattr(cache, "_cache_removed", set())
    monkeypatch.setattr(cache, "_cache_replace_generation", 0)
    assert getattr(cache, operation)() == 0
    assert sentinel.exists()


def test_deeply_nested_manifest_reports_failure_instead_of_crashing(tmp_path):
    (tmp_path / "project.json").write_text('{"rows": []}', encoding="utf-8")
    (tmp_path / "cyoa_manifest.json").write_text("[" * 20000 + "0" + "]" * 20000, encoding="utf-8")
    ok, report = package.verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid or unreadable cyoa_manifest.json" in report


def test_gui_worker_setup_failure_still_finalizes(monkeypatch, tmp_path):
    from cyoa_downloader_app.gui import final_behaviors as gui

    callbacks = []
    statuses = []
    events = []
    worker = SimpleNamespace(
        _v46_set_event_sink=lambda: None,
        _on_ytdlp_progress=lambda *args: None,
        _record_speed_bytes=lambda *args: None,
        _v46_enqueue_progress=events.append,
        _set_status=statuses.append,
        _run_on_ui_thread=callbacks.append,
        _done=lambda: None,
    )

    def failed_setup(*args, **kwargs):
        raise OSError("cannot save network settings")

    monkeypatch.setattr(gui, "_set_cloudflare_config", failed_setup)
    monkeypatch.setattr(gui, "_load_settings", lambda: {})
    monkeypatch.setattr(state, "_gui_speed_cb", None)
    monkeypatch.setattr(state, "_ytdlp_gui_progress_cb", None)
    gui._v46_worker(worker, [], "embed", 0, 1, str(tmp_path), False, False, "off", False, False, 0, False)
    assert callbacks == [worker._done]
    assert any("Failed" in status for status in statuses)
    assert any(event.get("type") == "job_failed" for event in events)


def test_gui_run_failure_does_not_report_completed(monkeypatch):
    from cyoa_downloader_app.gui import final_behaviors as gui

    events = []
    button = SimpleNamespace(configure=lambda **kwargs: None)
    worker = SimpleNamespace(
        _paused=threading.Event(),
        _pause_btn=button,
        _dl_btn=button,
        _v46_cancel_btn=button,
        _v46_copy_error_btn=button,
        _status_var=SimpleNamespace(get=lambda: "Failed — cannot save settings"),
        _last_results=[],
        _v46_worker_failed=True,
        _cancel_event=threading.Event(),
        _v46_enqueue_progress=events.append,
        _v46_close_pending=False,
    )
    monkeypatch.setattr(gui, "_send_desktop_notification", lambda *args: None)
    monkeypatch.setattr(state, "_gui_speed_cb", None)
    monkeypatch.setattr(state, "_ytdlp_gui_progress_cb", None)
    gui._v46_done(worker)
    assert events[-1]["state"] == "FAILED"


@pytest.mark.parametrize("target", ["http://example.test/", "https://example.test/"])
def test_flaresolverr_environment_bypass_matches_requests(monkeypatch, target):
    monkeypatch.setattr(state, "_proxy_mode", "inherit_env")
    monkeypatch.setattr(state, "_active_proxy", None)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080")
    monkeypatch.setenv("NO_PROXY", "example.test")
    monkeypatch.setattr(cloudflare, "legacy", lambda: SimpleNamespace(
        _FLARESOLVERR_PROXY_MODE="inherit", _get_active_proxy=proxy._get_active_proxy,
    ))
    assert cloudflare._flaresolverr_payload_proxy(target) is None


def test_flaresolverr_does_not_reuse_https_only_manual_proxy_for_http(monkeypatch):
    monkeypatch.setattr(state, "_proxy_mode", "manual")
    monkeypatch.setattr(state, "_active_proxy", None)
    monkeypatch.setattr(state, "_proxy_http", None)
    monkeypatch.setattr(state, "_proxy_https", "http://proxy.test:8080")
    monkeypatch.setattr(state, "_proxy_no_proxy", "")
    monkeypatch.setattr(cloudflare, "legacy", lambda: SimpleNamespace(
        _FLARESOLVERR_PROXY_MODE="inherit", _get_active_proxy=proxy._get_active_proxy,
    ))
    assert cloudflare._flaresolverr_payload_proxy("http://example.test/") is None


@pytest.mark.parametrize("suffix", ["%61uth/signout", "%6cogout", "../outside", "%2e%2e/outside", "admin;session=1/"])
def test_route_scope_blocks_encoded_actions_and_traversal(tmp_path, suffix):
    crawler = RouteCrawler(SimpleNamespace(
        start_url="https://example.test/game/",
        start_html_local=str(tmp_path / "index.html"),
        output_folder=str(tmp_path),
    ), ArchivePolicy(strategy="smart"))
    assert not crawler._is_allowed("https://example.test/game/" + suffix)


def test_route_same_origin_accepts_default_https_port(tmp_path):
    crawler = RouteCrawler(SimpleNamespace(
        start_url="https://example.test:443/game/",
        start_html_local=str(tmp_path / "index.html"),
        output_folder=str(tmp_path),
    ), ArchivePolicy(strategy="smart"))
    assert crawler._is_allowed("https://example.test/game/choice")


def test_route_error_response_is_closed(tmp_path):
    response = requests.Response()
    response.status_code = 404
    closed = []
    response.close = lambda: closed.append(True)
    crawler = RouteCrawler(SimpleNamespace(
        start_url="https://example.test/game/",
        start_html_local=str(tmp_path / "index.html"),
        output_folder=str(tmp_path),
        _fetch=lambda url: response,
    ), ArchivePolicy(strategy="classic"))
    assert crawler._fetch_html(crawler.start_url) is None
    assert closed == [True]


@pytest.mark.parametrize("name", ["art%20work.png", "literal%2e%2e.png", "COM¹.png", "CON .png"])
def test_output_names_and_manifests_are_windows_safe(tmp_path, name):
    safe_name = package.clean_url_path_component(name)
    if name.startswith(("COM", "CON")):
        assert safe_name.startswith("_")
    # A literal percent filename is legal and must round-trip its checksum.
    disk_name = name if name.startswith(("art", "literal")) else safe_name
    (tmp_path / disk_name).write_bytes(b"fixture")
    assert package.write_package_manifest(str(tmp_path))[0]
    assert package.verify_output_package(str(tmp_path))[0]


def test_reusable_browser_checks_tls_and_refreshes_changed_proxy(monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    contexts = []
    launches = []
    closed = []
    active_proxy = {"server": "http://proxy.test:8080"}
    page = SimpleNamespace(close=lambda: closed.append("page"))

    def new_context(**kwargs):
        contexts.append(kwargs)
        return SimpleNamespace(new_page=lambda: page, close=lambda: closed.append("context"))

    def launch(**kwargs):
        launches.append(kwargs)
        return SimpleNamespace(new_context=new_context, close=lambda: closed.append("browser"))

    runtime = SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=lambda: closed.append("runtime"))
    monkeypatch.setattr(playwright, "sync_playwright", lambda: SimpleNamespace(start=lambda: runtime))
    monkeypatch.setattr(browser, "_get_browser_proxy_config", lambda url: active_proxy)
    session = browser.BrowserFetchSession()
    try:
        session._ensure("https://example.test/")
        assert contexts[0].get("ignore_https_errors", False) is False
        active_proxy = {}
        session._ensure("http://localhost/")
        assert len(launches) == 2
        assert "proxy" not in launches[-1]
        assert "browser" in closed
        active_proxy = None
        with pytest.raises(RuntimeError, match="unsupported"):
            session._ensure("https://example.test/")
    finally:
        session.close()


def test_reusable_browser_does_not_reuse_old_proxy(monkeypatch):
    session = browser.BrowserFetchSession()
    session._page = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(browser, "_get_browser_proxy_config", lambda url: None)
    try:
        with pytest.raises(RuntimeError, match="unsupported"):
            session._ensure("https://example.test/")
    finally:
        session.close()
