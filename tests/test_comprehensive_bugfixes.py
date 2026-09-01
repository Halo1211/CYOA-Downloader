import ast
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from cyoa_downloader_app.core.output import _cleanup_recent_part_files
from cyoa_downloader_app.cli import _safe_console_print
from cyoa_downloader_app.core.url_utils import canonicalize_url
from cyoa_downloader_app.diagnostics import updates
from cyoa_downloader_app.download import image_pipeline
from cyoa_downloader_app.download import asset_scan, fonts
from cyoa_downloader_app.download.package import verify_output_package, write_package_manifest
from cyoa_downloader_app.download.website import WebsiteDownloader
from cyoa_downloader_app.importers.batch import _google_sheet_csv_export_url
from cyoa_downloader_app.integrations import ai_core
from cyoa_downloader_app.integrations.offline_viewers import registry
from cyoa_downloader_app.integrations.offline_viewers import injector
from cyoa_downloader_app.network import fetch_base
from cyoa_downloader_app.network import fetch as fetch_wrapper
from cyoa_downloader_app.gui.app import CYOADownloaderGUI
from cyoa_downloader_app.importers import batch as batch_importer
from cyoa_downloader_app.project import cyoa_cafe, discover
from cyoa_downloader_app.project.cyoap_vue import BeautifulSoup
from cyoa_downloader_app.project.parse import (
    looks_like_project_payload,
    normalize_project_payload_text,
)
from cyoa_downloader_app.storage import cache as cache_store
from cyoa_downloader_app.storage import history as history_store


class FakeResponse:
    def __init__(self, status=200, headers=None, content=b"ok"):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.encoding = "utf-8"
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=131072):
        yield self.content

    def __bool__(self):
        return True


def test_google_sheet_url_conversion_handles_fragment_gid():
    url = "https://docs.google.com/spreadsheets/d/sheet_123/edit#gid=456"
    assert _google_sheet_csv_export_url(url).endswith("format=csv&gid=456")


def test_cyoap_vue_has_a_working_html_parser():
    soup = BeautifulSoup("<html><script src='dist/app.js'></script></html>", "html.parser")
    assert soup.find("script")["src"] == "dist/app.js"


def test_wrapped_app_project_payload_is_recognized_and_preserved():
    raw = json.dumps({"app": {"rows": [], "backpack": [], "title": "Wrapped", "image": "x.png"}})
    assert looks_like_project_payload(raw)
    normalized = normalize_project_payload_text(raw)
    assert json.loads(normalized)["app"]["title"] == "Wrapped"


def test_embedded_js_state_fragment_is_not_project_payload():
    fragment = "{linkedObjects:[],mainDiv:t.mainDiv,bCreatorMode:!1,isBackpack:a(),isOverDlg:!1,isOverImg:!1}"
    assert not looks_like_project_payload(fragment)


def test_local_viewer_name_alone_does_not_match_unrelated_html(monkeypatch):
    monkeypatch.setattr(registry, "_load_viewers_manifest", lambda: {
        "local": {"name": "LocalViewer", "viewer_type": "icc_plus"}
    })
    assert registry.get_viewer_for_site("<html><p>unrelated</p></html>", "embed") is None


def test_malformed_viewer_manifest_entries_are_ignored(tmp_path, monkeypatch):
    manifest = tmp_path / "viewers.json"
    manifest.write_text(json.dumps({
        "broken": "not-an-object",
        "usable": {"name": 42, "viewer_type": "icc_plus", "zip_filename": []},
    }), encoding="utf-8")
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))
    loaded = registry._load_viewers_manifest()
    assert "broken" not in loaded
    assert loaded["usable"]["name"] == "usable"
    assert loaded["usable"]["zip_filename"] == ""


def test_viewer_manifest_rejects_unsafe_archive_and_entry_paths(tmp_path, monkeypatch):
    manifest = tmp_path / "viewers.json"
    manifest.write_text(json.dumps({
        "archive_escape": {
            "zip_filename": "../outside.zip",
            "entry_point": "index.html",
        },
        "registry_delete": {
            "zip_filename": "viewers.json",
            "entry_point": "index.html",
        },
        "entry_escape": {
            "zip_filename": "valid.zip",
            "entry_point": "../../outside.html",
        },
        "valid": {
            "zip_filename": "valid.zip",
            "entry_point": "nested/index.html",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(manifest))

    loaded = registry._load_viewers_manifest()

    assert set(loaded) == {"valid"}
    assert loaded["valid"]["entry_point"] == "nested/index.html"


def test_register_offline_viewer_rejects_traversal_members(tmp_path, monkeypatch):
    viewer_zip = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(viewer_zip, "w") as archive:
        archive.writestr("../escape.html", "bad")
        archive.writestr("index.html", "ok")
    store = tmp_path / "store"
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(store / "viewers.json"))

    assert registry.register_offline_viewer(str(viewer_zip)) is None
    assert not (store / viewer_zip.name).exists()


def test_unregister_offline_viewer_never_deletes_outside_registry(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"keep")
    monkeypatch.setattr(registry, "_VIEWERS_DIR", str(store))
    monkeypatch.setattr(registry, "_VIEWERS_MANIFEST", str(store / "viewers.json"))
    monkeypatch.setattr(
        registry,
        "_load_viewers_manifest",
        lambda: {"unsafe": {"zip_filename": "../outside.zip"}},
    )
    monkeypatch.setattr(registry, "_save_viewers_manifest", lambda _manifest: None)

    assert registry.unregister_offline_viewer("unsafe", delete_zip=True) is True
    assert outside.read_bytes() == b"keep"


def test_offline_viewer_injector_rejects_unsafe_metadata_and_cleans_failed_output(
    tmp_path, monkeypatch,
):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(store))

    assert injector._apply_offline_viewer(
        str(output), "{}", {"zip_filename": "../outside.zip"}, file_name="unsafe"
    ) is None
    assert not (output / "unsafe_offline").exists()

    viewer_zip = store / "missing-entry.zip"
    with zipfile.ZipFile(viewer_zip, "w") as archive:
        archive.writestr("app.js", "console.log('ok')")
    assert injector._apply_offline_viewer(
        str(output),
        "{}",
        {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
        file_name="missing",
    ) is None
    assert not (output / "missing_offline").exists()


def test_offline_viewer_injector_revalidates_archive_at_use_time(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    viewer_zip = store / "viewer.zip"
    with zipfile.ZipFile(viewer_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", b"0" * (2 * 1024 * 1024))

    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(store))
    assert injector._apply_offline_viewer(
        str(output),
        "{}",
        {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
        file_name="bomb",
    ) is None
    assert not (output / "bomb_offline").exists()


def test_offline_viewer_final_write_failure_removes_partial_folder(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    viewer_zip = store / "viewer.zip"
    with zipfile.ZipFile(viewer_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", "<html><head></head><body></body></html>")

    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(store))

    def fail_final_write(path, text, encoding="utf-8"):
        if str(path).endswith("index.html"):
            raise OSError("disk full")
        return path

    monkeypatch.setattr(injector, "atomic_write_text", fail_final_write)
    assert injector._apply_offline_viewer(
        str(output),
        "{}",
        {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
        file_name="write-fail",
    ) is None
    assert not (output / "write-fail_offline").exists()


def test_offline_viewer_rejects_invalid_project_before_extracting(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    viewer_zip = store / "viewer.zip"
    with zipfile.ZipFile(viewer_zip, "w") as archive:
        archive.writestr("index.html", "<html></html>")

    monkeypatch.setattr(injector, "_VIEWERS_DIR", str(store))
    assert injector._apply_offline_viewer(
        str(output),
        "{broken",
        {"zip_filename": viewer_zip.name, "entry_point": "index.html"},
        file_name="invalid-project",
    ) is None
    assert not (output / "invalid-project_offline").exists()


def test_cleanup_removes_only_true_part_suffix(tmp_path):
    legitimate = tmp_path / "chapter.part.png"
    temporary = tmp_path / "chapter.png.1.2.part"
    legitimate.write_bytes(b"png")
    temporary.write_bytes(b"partial")
    assert _cleanup_recent_part_files(str(tmp_path), time.time() - 1) == 1
    assert legitimate.exists()
    assert not temporary.exists()


def test_malformed_manifest_entry_reports_failure_instead_of_crashing(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text(json.dumps({
        "files": {"asset.bin": "not-an-object"}, "file_count": 1,
    }), encoding="utf-8")
    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid manifest entry" in report


def test_invalid_manifest_json_is_not_treated_as_absent(tmp_path):
    (tmp_path / "asset.bin").write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text("{broken", encoding="utf-8")
    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid or unreadable" in report


def test_manifest_rejects_missing_checksum_and_wrong_recorded_size(tmp_path):
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"asset")
    (tmp_path / "cyoa_manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "file_count": 1,
        "files": {"asset.bin": {"sha256": "", "size": 5}},
    }), encoding="utf-8")

    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "invalid manifest checksum: asset.bin" in report

    digest = hashlib.sha256(b"asset").hexdigest()
    (tmp_path / "cyoa_manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "file_count": 1,
        "files": {"asset.bin": {"sha256": digest, "size": 999}},
    }), encoding="utf-8")
    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "size mismatch (corrupt/modified): asset.bin" in report


def test_manifest_includes_nested_asset_with_manifest_filename(tmp_path):
    nested = tmp_path / "assets" / "cyoa_manifest.json"
    nested.parent.mkdir()
    nested.write_text('{"asset": true}', encoding="utf-8")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    ok, _message = write_package_manifest(str(tmp_path))
    assert ok
    manifest = json.loads((tmp_path / "cyoa_manifest.json").read_text(encoding="utf-8"))
    assert "assets/cyoa_manifest.json" in manifest["files"]
    verify_ok, report = verify_output_package(str(tmp_path))
    assert verify_ok, report


def test_package_verifier_rejects_portability_case_mismatch(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "Hero.png").write_bytes(b"png")
    (tmp_path / "project.json").write_text(json.dumps({
        "rows": [],
        "images": [{"path": "images/hero.png"}],
    }), encoding="utf-8")

    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "asset reference case mismatch: images/hero.png" in report

    (tmp_path / "project.json").unlink()
    (tmp_path / "index.html").write_text(
        '<img src="images/hero.png">', encoding="utf-8"
    )
    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "missing asset: images/hero.png" in report


def test_package_manifest_never_hashes_path_outside_package(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private")
    (package / "asset.bin").write_bytes(b"asset")
    (package / "cyoa_manifest.json").write_text(json.dumps({
        "files": {
            "asset.bin": {"sha256": "invalid"},
            "../outside.bin": {"sha256": "also-invalid"},
        },
        "file_count": 2,
    }), encoding="utf-8")

    hashed = []
    from cyoa_downloader_app.download import package as package_module
    real_hash = package_module._hash_file_sha256

    def tracking_hash(path):
        hashed.append(str(Path(path).resolve()))
        return real_hash(path)

    monkeypatch.setattr(package_module, "_hash_file_sha256", tracking_hash)
    ok, report = verify_output_package(str(package))
    assert not ok
    assert "unsafe path in manifest" in report
    assert str(outside.resolve()) not in hashed


def test_project_asset_reference_requires_matching_directory(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "hero.png").write_bytes(b"image")
    (tmp_path / "project.json").write_text(json.dumps({
        "rows": [],
        "images": [{"path": "images/hero.png"}],
    }), encoding="utf-8")

    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "missing asset: images/hero.png" in report


def test_project_asset_reference_rejects_parent_traversal(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({
        "rows": [],
        "images": [{"path": "../secret.png"}],
    }), encoding="utf-8")

    ok, report = verify_output_package(str(tmp_path))
    assert not ok
    assert "unsafe local asset reference" in report


def test_ipv6_canonicalization_restores_brackets():
    assert canonicalize_url("http://[2001:db8::1]:8080/a") == "http://[2001:db8::1]:8080/a"


def test_fetch_wrapper_closes_response_when_cancelled_after_request(monkeypatch):
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.test/file"
    response.headers["Content-Length"] = "4"
    response._content = b"data"
    closed = []
    response.close = lambda: closed.append(True)
    checks = 0

    def raise_on_second_check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise DownloadCancelledError("cancelled")

    bridge = SimpleNamespace(
        _raise_if_cancelled=raise_on_second_check,
        _v46_fetch_response_legacy=lambda *_args, **_kwargs: response,
        validate_response_content_length=lambda *_args: None,
        _emit_progress_event=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(fetch_wrapper, "legacy", lambda: bridge)

    with pytest.raises(DownloadCancelledError):
        fetch_wrapper.fetch_response(response.url, as_bytes=True)
    assert closed == [True]


def test_gui_start_passes_default_mode_value_not_tk_variable(tmp_path, monkeypatch):
    from cyoa_downloader_app.gui import final_behaviors

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    class Widget:
        def configure(self, **_kwargs):
            return None

    class Telemetry:
        def reset(self, _count):
            return None

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            captured.update(target=target, args=args, name=name, daemon=daemon)

        def start(self):
            captured["started"] = True

    dummy = SimpleNamespace(
        _is_running=False,
        _queue_data=[
            {"url": "https://example.test/game", "filename": ""},
            {"url": "https://example.test/game", "filename": "second"},
        ],
        _wait_var=Var("1"),
        _threads_var=Var("2"),
        _bw_var=Var("0"),
        _outdir_var=Var(str(tmp_path)),
        _prepare_ytdlp_cookies=lambda: True,
        # The final sidebar stores this as a plain string.  This exact runtime
        # shape previously crashed Download All after cookie preparation.
        _mode_var="auto",
        _cancel_event=__import__("threading").Event(),
        _paused=__import__("threading").Event(),
        _v46_telemetry=Telemetry(),
        _v46_set_event_sink=lambda: None,
        _v46_enqueue_progress=lambda _event: None,
        _dl_btn=Widget(),
        _pause_btn=Widget(),
        _v46_cancel_btn=Widget(),
        _v46_copy_error_btn=Widget(),
        _status_var=Var(""),
        _worker=lambda *_args: None,
        _fonts_var=Var(False),
        _analyse_var=Var(False),
        _cf_mode_var=Var("off"),
        _http2_var=Var(False),
        _ytdlp_var=Var(False),
        _cyoa_mgr_var=Var(False),
    )
    dummy._paused.set()
    dummy._status_var.set = lambda value: setattr(dummy._status_var, "value", value)
    monkeypatch.setattr(final_behaviors.threading, "Thread", FakeThread)

    final_behaviors._v46_start(dummy)

    assert captured["started"] is True
    assert captured["args"][1] == "auto"
    assert isinstance(captured["args"][1], str)
    run_items = captured["args"][0]
    assert all(item["_run_requested_mode"] == "auto" for item in run_items)
    assert all(item["mode"] == "auto" for item in run_items)
    assert run_items[0]["_resume_key"] != run_items[1]["_resume_key"]


def test_download_start_cookie_prepare_only_activates_saved_path(tmp_path, monkeypatch):
    import os
    from cyoa_downloader_app.gui import app as gui_app

    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    updates = []

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    dummy = SimpleNamespace(
        _ytdlp_cookies_var=Var(str(cookie_path)),
        _language="en",
    )
    monkeypatch.setattr(gui_app, "os", os, raising=False)
    monkeypatch.setattr(
        gui_app,
        "_update_setting",
        lambda key, value: updates.append((key, value)),
        raising=False,
    )
    monkeypatch.delenv("CYOA_YTDLP_COOKIES", raising=False)

    assert CYOADownloaderGUI._save_ytdlp_cookie_setting(
        dummy, show_error=True, persist=False,
    )
    assert updates == []
    assert os.environ["CYOA_YTDLP_COOKIES"] == str(cookie_path.resolve())


def test_download_all_surfaces_pre_worker_callback_failures(monkeypatch):
    import logging
    import tkinter.messagebox as messagebox
    from cyoa_downloader_app.gui import app as gui_app

    class Widget:
        def __init__(self):
            self.values = {}

        def configure(self, **kwargs):
            self.values.update(kwargs)

    class Status:
        value = "Idle"

        def set(self, value):
            self.value = value

    errors = []
    dummy = SimpleNamespace(
        _dispatch_gui_patch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("broken start state")
        ),
        _start_base=lambda: None,
        _is_running=False,
        _dl_btn=Widget(),
        _pause_btn=Widget(),
        _status_var=Status(),
    )
    monkeypatch.setattr(gui_app, "logger", logging.getLogger("test-gui-start"), raising=False)
    monkeypatch.setattr(messagebox, "showerror", lambda title, body: errors.append((title, body)))

    CYOADownloaderGUI._start(dummy)

    assert dummy._is_running is False
    assert dummy._dl_btn.values["state"] == "normal"
    assert dummy._pause_btn.values["state"] == "disabled"
    assert "broken start state" in dummy._status_var.value
    assert errors and errors[0][0] == "Download All"


def test_auto_detect_cancel_waits_for_active_probe_workers(monkeypatch):
    from cyoa_downloader_app.core import cancellation
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    cancel_event = __import__("threading").Event()
    slow_worker_done = __import__("threading").Event()

    def fake_detect(url):
        if url.endswith("fast"):
            cancel_event.set()
            return "website_folder"
        assert cancel_event.wait(2)
        time.sleep(0.05)
        slow_worker_done.set()
        raise DownloadCancelledError("cancelled probe")

    monkeypatch.setattr(discover, "auto_detect_mode", fake_detect)
    cancellation.set_progress_event_sink(None, cancel_event)
    try:
        with pytest.raises(DownloadCancelledError):
            discover.auto_detect_modes_batch(
                [
                    {"url": "https://example.test/slow", "mode": "auto"},
                    {"url": "https://example.test/fast", "mode": "auto"},
                ],
                max_workers=2,
            )
        assert slow_worker_done.is_set()
    finally:
        cancellation.clear_progress_event_sink()


def test_entry_html_failure_is_fatal(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    monkeypatch.setattr(downloader, "_fetch", lambda _url: None)
    with pytest.raises(RuntimeError, match="entry HTML"):
        downloader.download()
    assert not (tmp_path / "index.html").exists()


def test_website_css_reuse_does_not_refetch_local_font(tmp_path, monkeypatch):
    css_dir = tmp_path / "css"
    fonts_dir = tmp_path / "fonts"
    css_dir.mkdir()
    fonts_dir.mkdir()
    (fonts_dir / "Roboto.woff2").write_bytes(b"font")
    css_path = css_dir / "main.css"
    css = '@font-face { font-family: Roboto; src: url("../fonts/Roboto.woff2"); }'
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("already-local font was fetched again")

    monkeypatch.setattr(downloader, "_download_asset", unexpected_fetch)
    assert downloader._process_css(
        css,
        "https://example.test/game/css/main.css",
        str(css_path),
    ) == css


def test_website_css_root_fallback_recovers_viewer_root_asset(tmp_path, monkeypatch):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    calls = []

    class FakeResponse:
        headers = {"Content-Type": "image/webp", "Content-Length": "5"}
        url = "https://example.test/game/mafia_headquarters.webp"

        def iter_content(self, chunk_size=0):
            yield b"image"

        def close(self):
            return None

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("/assets/mafia_headquarters.webp"):
            return None
        return FakeResponse()

    monkeypatch.setattr(downloader, "_fetch", fake_fetch)
    local = downloader._download_asset(
        "mafia_headquarters.webp",
        preferred_kind="images",
        referrer_url="https://example.test/game/assets/index.css",
    )

    assert local == str(tmp_path / "mafia_headquarters.webp")
    assert calls == [
        "https://example.test/game/assets/mafia_headquarters.webp",
        "https://example.test/game/mafia_headquarters.webp",
    ]
    assert not any(
        item.get("url") == "https://example.test/game/assets/mafia_headquarters.webp"
        for item in downloader._failed_items
    )


def test_react_app_bundle_rewrites_only_known_downloaded_absolute_urls(tmp_path):
    downloader = WebsiteDownloader(
        "https://example.test/game/", str(tmp_path), archive_strategy="classic"
    )
    local_asset = tmp_path / "files" / "image.png"
    local_asset.parent.mkdir()
    local_asset.write_bytes(b"image")
    js_local = tmp_path / "static" / "js" / "main.12345678.chunk.js"
    js_local.parent.mkdir(parents=True)
    downloader._downloaded["https://cdn.example/image.png"] = str(local_asset)

    bundle = (
        "var data={src:'https://cdn.example/image.png', "
        "missing:'https://cdn.example/missing.png', "
        "chunk:'static/js/other.12345678.chunk.js'};"
    )
    rewritten = downloader._process_js(
        bundle,
        "https://example.test/game/static/js/main.12345678.chunk.js",
        str(js_local),
    )

    assert "https://cdn.example/image.png" not in rewritten
    assert "../../files/image.png" in rewritten
    assert "https://cdn.example/missing.png" in rewritten
    assert "static/js/other.12345678.chunk.js" in rewritten


def test_deep_scan_results_seed_website_cache_and_localize_without_refetch(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "pic.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"deep-scanned-image")
    css_path = tmp_path / "css" / "main.css"
    css_path.parent.mkdir()
    css_path.write_text(
        'body { background: url("https://example.test/game/images/pic.png"); }',
        encoding="utf-8",
    )
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    downloader._register_deep_scan_results({
        "https://example.test/game/images/pic.png": "images/pic.png",
    })

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("deep-scanned asset was fetched again")

    monkeypatch.setattr(downloader, "_fetch", unexpected_fetch)
    downloader.localize_existing_text_assets()

    assert "../images/pic.png" in css_path.read_text(encoding="utf-8")


def test_localize_json_embedded_downloaded_urls_without_destroying_label(tmp_path):
    downloader = WebsiteDownloader("https://example.test/game/", str(tmp_path))
    image_path = tmp_path / "images" / "luna.gif"
    image_path.parent.mkdir()
    image_path.write_bytes(b"gif")
    project_path = tmp_path / "project.json"
    project_path.write_text(
        '{"image":"Luna tongue https:/cdn.example/luna.gif",'
        '"missing":"https://cdn.example/missing.gif"}',
        encoding="utf-8",
    )
    downloader._register_deep_scan_results({
        "https://cdn.example/luna.gif": "images/luna.gif",
    })

    downloader.localize_existing_text_assets()
    localized = project_path.read_text(encoding="utf-8")

    assert "Luna tongue images/luna.gif" in localized
    assert "https://cdn.example/missing.gif" in localized


def test_download_asset_retries_root_assets_under_viewer_route(tmp_path, monkeypatch):
    downloader = WebsiteDownloader(
        "https://example.test/overlord_0.8.8/",
        str(tmp_path),
        archive_strategy="classic",
    )
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("/assets/images/races/r_zombie.png") and "/overlord_0.8.8/" not in url:
            return None
        return FakeResponse(headers={"Content-Type": "image/png"}, content=b"png")

    monkeypatch.setattr(downloader, "_fetch", fake_fetch)
    local = downloader._download_asset(
        "https://example.test/assets/images/races/r_zombie.png",
        preferred_kind="images",
    )

    assert local == str(tmp_path / "assets" / "images" / "races" / "r_zombie.png")
    assert calls == [
        "https://example.test/assets/images/races/r_zombie.png",
        "https://example.test/overlord_0.8.8/assets/images/races/r_zombie.png",
    ]


def test_dns_resolved_internal_target_is_blocked_but_same_origin_local_is_allowed(monkeypatch):
    monkeypatch.setattr(ai_core.socket, "getaddrinfo", lambda *_a, **_k: [
        (2, 1, 6, "", ("127.0.0.1", 0)),
    ])
    ai_core._set_allow_internal_hosts(False)
    assert ai_core._ssrf_block_cross_origin("http://alias.test/secret", "https://public.test/game")
    assert not ai_core._ssrf_block_cross_origin("http://alias.test/a", "http://alias.test/b")
    ai_core._set_allow_internal_hosts(True)
    try:
        assert not ai_core._ssrf_block_cross_origin("http://alias.test/secret", "https://public.test/game")
    finally:
        ai_core._set_allow_internal_hosts(False)


def test_cyoa_cafe_candidate_guard_checks_dns_resolution(monkeypatch):
    monkeypatch.setattr(cyoa_cafe, "_host_resolves_internal", lambda host: host == "alias.test")
    resolver = cyoa_cafe.CYOACafeResolver(fetcher=lambda *_a, **_k: None)
    allowed, reason = resolver._candidate_allowed("https://alias.test/project.json")
    assert not allowed
    assert "internal" in reason


def test_cyoa_cafe_resolver_closes_probe_responses():
    response = FakeResponse(200, {"Content-Type": "text/html"}, b'<div id="app"></div>')
    resolver = cyoa_cafe.CYOACafeResolver(fetcher=lambda *_a, **_k: response)
    assert resolver.resolve("https://demo.cyoa.cafe/game/") == "https://demo.cyoa.cafe/game/"
    assert response.closed
    assert resolver._responses == {}


def test_cyoa_cafe_metadata_cache_cannot_reuse_another_games_viewer(monkeypatch):
    source = "https://cyoa.cafe/game/current123"
    record = {
        "id": "current123",
        "iframe_url": "https://viewer.example/current/",
    }

    monkeypatch.setattr(cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})

    def fake_fetch(url, **_kwargs):
        if "/api/collections/games/records/" in url:
            return FakeResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps(record).encode(),
            )
        return FakeResponse(200, {"Content-Type": "text/html"}, b'<div id="app"></div>')

    resolver = cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)
    resolver._cache_put(source, "https://viewer.example/previous/")

    assert resolver.resolve(source) == "https://viewer.example/current/"


def test_cyoa_cafe_slug_route_uses_pocketbase_slug_filter(monkeypatch):
    source = "https://cyoa.cafe/game/demon-s-blessing-expansion"
    record = {
        "id": "l9x4vjh3eid3xcl",
        "slug": "demon-s-blessing-expansion",
        "iframe_url": "https://viewer.example/demon/",
    }
    calls = []

    monkeypatch.setattr(cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        if "/records/demon-s-blessing-expansion" in url:
            return FakeResponse(404, {"Content-Type": "application/json"}, b"{}")
        if "filter=slug%3D%27demon-s-blessing-expansion%27" in url:
            return FakeResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps({"items": [record]}).encode(),
            )
        if url == record["iframe_url"]:
            return FakeResponse(200, {"Content-Type": "text/html"}, b"<html><div id='app'></div></html>")
        return FakeResponse(404, {"Content-Type": "text/html"}, b"")

    resolver = cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)

    assert resolver.resolve(source) == record["iframe_url"]
    assert any("filter=slug%3D%27demon-s-blessing-expansion%27" in url for url in calls)


def test_cyoa_cafe_accepts_static_core_cyoa_without_app_signature(monkeypatch):
    source = "https://cyoa.cafe/game/static123"
    record = {
        "id": "static123",
        "iframe_url": "https://core.cyoa.cafe/fairy-feminization/",
    }
    html = b"<title>[CYOA] Fairy Feminization</title><main><input id='a'><label for='a'>Choice</label></main>"

    monkeypatch.setattr(cyoa_cafe, "_CYOA_CAFE_CACHE", {})
    monkeypatch.setattr(cyoa_cafe, "_CYOA_CAFE_RECORD_CACHE", {})

    def fake_fetch(url, **_kwargs):
        if "/api/collections/games/records/" in url:
            return FakeResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps(record).encode(),
            )
        return FakeResponse(200, {"Content-Type": "text/html"}, html)

    resolver = cyoa_cafe.CYOACafeResolver(fetcher=fake_fetch)
    assert resolver.resolve(source) == record["iframe_url"]


def test_remote_batch_import_closes_response(monkeypatch):
    response = FakeResponse(
        200,
        {"Content-Type": "text/csv"},
        b"url,filename\nhttps://example.test/game,story\n",
    )
    monkeypatch.setattr(batch_importer, "fetch_response", lambda *_a, **_k: response)
    assert batch_importer.import_queue_items_from_source("https://example.test/list.csv") == [{
        "url": "https://example.test/game",
        "filename": "story",
        "mode": "",
    }]
    assert response.closed


def test_remote_batch_import_contains_malformed_csv_and_propagates_cancellation(monkeypatch):
    from cyoa_downloader_app.core.progress import DownloadCancelledError

    oversized_field = b"url\nhttps://example.test/" + (b"a" * 140_000) + b"\n"
    response = FakeResponse(200, {"Content-Type": "text/csv"}, oversized_field)
    monkeypatch.setattr(batch_importer, "fetch_response", lambda *_a, **_k: response)
    assert batch_importer.import_queue_items_from_source(
        "https://example.test/list.csv"
    ) == []
    assert response.closed

    def cancelled_fetch(*_args, **_kwargs):
        raise DownloadCancelledError("cancelled remote import")

    monkeypatch.setattr(batch_importer, "fetch_response", cancelled_fetch)
    with pytest.raises(DownloadCancelledError):
        batch_importer.import_queue_items_from_source("https://example.test/list.csv")


def test_discovered_project_urls_block_cross_origin_internal_hosts(monkeypatch):
    html = '<script>fetch("http://127.0.0.1:9000/project.json")</script>'
    assert discover.find_candidate_urls_in_text(html, "https://public.test/game/") == []
    local = discover.find_candidate_urls_in_text(
        '<script>fetch("project.json")</script>', "http://127.0.0.1:8000/game/",
    )
    assert local == ["http://127.0.0.1:8000/game/project.json"]

    calls = []
    monkeypatch.setattr(discover, "fetch_response", lambda *_a, **_k: calls.append(True))
    assert discover.try_project_candidate(
        "http://127.0.0.1:9000/project.json",
        source_url="https://public.test/game/",
    ) == (None, "")
    assert calls == []


def _prepare_fetch_base(monkeypatch, session):
    logger = SimpleNamespace(warning=lambda *_a, **_k: None,
                             error=lambda *_a, **_k: None,
                             info=lambda *_a, **_k: None,
                             debug=lambda *_a, **_k: None)
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(
        logger=logger, _CLOUDFLARE_MODE="off",
    ))
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(fetch_base, "get_headers_for_url", lambda _url: {})
    monkeypatch.setattr(fetch_base, "_get_shared_session", lambda **_k: session)
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda _host: False)


def test_fetch_blocks_redirect_to_private_target(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse(302, {"Location": "http://127.0.0.1:9000/admin"})

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    assert fetch_base.base_fetch_response("https://public.test/start") is None
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


def test_fetch_keeps_verified_public_redirects_working(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                return FakeResponse(302, {"Location": "https://cdn.public.test/file"})
            return FakeResponse(200, {"Content-Type": "application/octet-stream"}, b"asset")

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    response = fetch_base.base_fetch_response(
        "https://public.test/start",
        as_bytes=True,
        extra_headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Trace": "kept",
        },
    )
    assert response.content == b"asset"
    assert [call[0] for call in session.calls] == [
        "https://public.test/start", "https://cdn.public.test/file",
    ]
    assert all(call[1]["verify"] is True for call in session.calls)
    redirected_headers = session.calls[1][1]["headers"]
    assert "Authorization" not in redirected_headers
    assert "Cookie" not in redirected_headers
    assert redirected_headers["X-Trace"] == "kept"


def test_fetch_blocks_redirect_to_non_http_scheme(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return FakeResponse(302, {"Location": "file:///etc/passwd"})

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    assert fetch_base.base_fetch_response("https://public.test/start") is None
    assert session.calls == ["https://public.test/start"]


def test_fetch_never_retries_with_tls_verification_disabled(monkeypatch):
    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(kwargs)
            raise requests.exceptions.SSLError("bad certificate")

    session = Session()
    _prepare_fetch_base(monkeypatch, session)
    assert fetch_base.base_fetch_response("https://public.test/start") is None
    assert len(session.calls) == 1
    assert session.calls[0]["verify"] is True


def test_fetch_return_error_response_preserves_all_http_errors(monkeypatch):
    responses = []

    class Session:
        def get(self, url, **kwargs):
            response = FakeResponse(404, {"Content-Type": "text/plain"}, b"missing")
            responses.append(response)
            return response

    _prepare_fetch_base(monkeypatch, Session())

    assert fetch_base.base_fetch_response("https://public.test/missing") is None
    assert responses[0].closed

    response = fetch_base.base_fetch_response(
        "https://public.test/missing", return_error_response=True,
    )
    assert response is responses[1]
    assert response.status_code == 404
    assert not response.closed


def test_cloudflare_auto_cloudscraper_keeps_requested_error_response(monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            if not kwargs.get("headers"):
                raise AssertionError("request headers should always be present")
            if self.use_cf:
                response = requests.Response()
                response.status_code = 500
                response.url = url
                response.headers["Content-Type"] = "text/plain"
                response._content = b"backend error"
                return response
            return FakeResponse(
                403,
                {"Content-Type": "text/html", "Server": "cloudflare"},
                b"Checking your browser",
            )

    logger = SimpleNamespace(warning=lambda *_a, **_k: None,
                             error=lambda *_a, **_k: None,
                             info=lambda *_a, **_k: None,
                             debug=lambda *_a, **_k: None)
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(
        logger=logger,
        _CLOUDFLARE_MODE="auto",
        _CLOUDFLARE_PRIORITY="cloudscraper_first",
    ))
    monkeypatch.setattr(fetch_base, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(fetch_base, "get_headers_for_url", lambda _url: {"User-Agent": "test"})
    monkeypatch.setattr(fetch_base, "_host_resolves_internal", lambda _host: False)

    def session_for_backend(*, use_cf=False):
        session = Session()
        session.use_cf = use_cf
        return session

    monkeypatch.setattr(fetch_base, "_get_shared_session", session_for_backend)
    monkeypatch.setattr(fetch_base, "fetch_via_flaresolverr", lambda *_a, **_k: None)

    response = fetch_base.base_fetch_response(
        "https://protected.test/page", return_error_response=True,
    )
    assert response is not None
    assert response.status_code == 500


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        ("flaresolverr_first", ["flaresolverr", "cloudscraper"]),
        ("cloudscraper_first", ["cloudscraper", "flaresolverr"]),
    ],
)
def test_cloudflare_auto_fallback_honors_priority(monkeypatch, priority, expected):
    class ChallengeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(bool(kwargs.get("headers")))
            return FakeResponse(
                403,
                {"Content-Type": "text/html", "Server": "cloudflare"},
                b"Checking your browser",
            )

    session = ChallengeSession()
    _prepare_fetch_base(monkeypatch, session)
    logger = SimpleNamespace(warning=lambda *_a, **_k: None,
                             error=lambda *_a, **_k: None,
                             info=lambda *_a, **_k: None,
                             debug=lambda *_a, **_k: None)
    monkeypatch.setattr(fetch_base, "legacy", lambda: SimpleNamespace(
        logger=logger, _CLOUDFLARE_MODE="auto", _CLOUDFLARE_PRIORITY=priority,
    ))
    calls = []

    def fake_flaresolverr(*_args, **_kwargs):
        calls.append("flaresolverr")
        return "CF_CHALLENGE"

    original_request = fetch_base._get_shared_session

    def request_with_backend_marker(*, use_cf=False):
        if use_cf:
            calls.append("cloudscraper")
        return session

    monkeypatch.setattr(fetch_base, "_get_shared_session", request_with_backend_marker)
    monkeypatch.setattr(fetch_base, "fetch_via_flaresolverr", fake_flaresolverr)
    assert fetch_base.base_fetch_response("https://protected.test/page") is None
    assert calls == expected


def test_queue_completion_removes_only_exact_duplicate_row():
    gui = CYOADownloaderGUI.__new__(CYOADownloaderGUI)
    gui._queue_data = [
        {"url": "https://same.test/game", "_queue_id": "first"},
        {"url": "https://same.test/game", "_queue_id": "second"},
        {"url": "https://new.test/game", "_queue_id": "new"},
    ]

    def remove_row(index):
        gui._queue_data.pop(index)

    gui._remove_row = remove_row
    assert gui._remove_queue_ids_from_queue({"first"}) == 1
    assert [item["_queue_id"] for item in gui._queue_data] == ["second", "new"]


def test_icc_project_image_pass_reuses_site_folder():
    source = Path(__file__).resolve().parents[1] / "cyoa_downloader_app" / "download" / "orchestrator.py"
    text = source.read_text(encoding="utf-8")
    icc_call = text[text.index("_, dl_result, _pi_urls = process_images("):]
    assert "site_folder=site_folder" in icc_call[:1200]


def test_process_images_reuses_existing_icc_image(monkeypatch, tmp_path):
    site = tmp_path / "icc"
    (site / "images").mkdir(parents=True)
    (site / "images" / "R1.avif").write_bytes(b"already downloaded")
    work = tmp_path / "work"
    raw = json.dumps({"rows": [{"objects": [{"image": "images/R1.avif"}]}]})

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("ICC image was fetched again instead of reused")

    monkeypatch.setattr(image_pipeline, "fetch_response", unexpected_fetch)
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://chuckeroo.cyoa.cafe/ucmccyoa/",
        download=True,
        temp_folder=str(work),
        site_folder=str(site),
        max_workers=1,
    )
    assert '"image":"images/R1.avif"' in downloaded
    assert not (work / "images").exists()


def test_process_images_reuses_existing_icc_audio(monkeypatch, tmp_path):
    site = tmp_path / "icc"
    (site / "audio").mkdir(parents=True)
    (site / "audio" / "click.mp3").write_bytes(b"already downloaded")
    work = tmp_path / "work"
    raw = json.dumps({"rows": [{"objects": [{"audio": "audio/click.mp3"}]}]})

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("ICC audio was fetched again instead of reused")

    monkeypatch.setattr(image_pipeline, "fetch_response", unexpected_fetch)
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://chuckeroo.cyoa.cafe/ucmccyoa/",
        download=True,
        temp_folder=str(work),
        site_folder=str(site),
        max_workers=1,
    )
    assert '"audio":"audio/click.mp3"' in downloaded
    assert not (work / "audio").exists()


def test_gallery_post_uses_gallery_dl_before_http_and_headless(
    monkeypatch, tmp_path,
):
    url = "https://www.furaffinity.net/view/12345/"
    calls = {"http": 0, "gallery": 0}

    def forbidden(*_args, **_kwargs):
        calls["http"] += 1
        return FakeResponse(403, {"Content-Type": "text/html"}, b"forbidden")

    def gallery_fetch(candidate):
        assert candidate == url
        calls["gallery"] += 1
        return b"gallery-image" * 16

    monkeypatch.setattr(image_pipeline, "fetch_response", forbidden)
    monkeypatch.setattr(image_pipeline, "_make_cookie_session", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_cancel_aware_sleep", lambda *_a: pytest.fail("auth failure backed off"))
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "furaffinity")
    monkeypatch.setattr(image_pipeline, "_fetch_via_gallery_dl", gallery_fetch)
    monkeypatch.setattr(
        image_pipeline,
        "_fetch_headless",
        lambda *_a, **_k: pytest.fail("headless ran before gallery-dl"),
    )

    raw = json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 0, "gallery": 1}
    assert '"image":"images/' in downloaded


def test_extensionless_image_page_gets_one_http_attempt_before_headless(
    monkeypatch, tmp_path,
):
    url = "https://example.test/gallery-entry"
    calls = {"http": 0, "headless": 0}

    def connection_timeout(*_args, **_kwargs):
        calls["http"] += 1
        raise requests.ConnectionError("timed out")

    def headless(candidate, **_kwargs):
        assert candidate == url
        calls["headless"] += 1
        return b"headless-image" * 16

    monkeypatch.setattr(image_pipeline, "fetch_response", connection_timeout)
    monkeypatch.setattr(image_pipeline, "_cancel_aware_sleep", lambda *_a: pytest.fail("single attempt slept"))
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(image_pipeline, "_fetch_headless", headless)

    raw = json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 1, "headless": 1}
    assert '"image":"images/' in downloaded


def test_exhausted_transport_retries_do_not_repeat_outer_image_loop(
    monkeypatch, tmp_path,
):
    url = "https://dead-cdn.example.test/image.jpg"
    calls = {"http": 0, "headless": 0}

    def exhausted(*_args, **_kwargs):
        calls["http"] += 1
        return None

    def headless(candidate, **_kwargs):
        assert candidate == url
        calls["headless"] += 1
        return b"recovered-image" * 16

    monkeypatch.setattr(image_pipeline, "fetch_response", exhausted)
    monkeypatch.setattr(image_pipeline, "_cancel_aware_sleep", lambda *_a: pytest.fail("outer loop slept"))
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(image_pipeline, "_fetch_headless", headless)

    raw = json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 1, "headless": 1}
    assert '"image":"images/' in downloaded


def test_transport_failed_domain_coalesces_failed_headless_probes(
    monkeypatch, tmp_path,
):
    urls = [
        "https://dead-cdn.example.test/one.jpg",
        "https://dead-cdn.example.test/two.jpg",
    ]
    calls = {"http": 0, "headless": 0}

    def exhausted(*_args, **_kwargs):
        calls["http"] += 1
        return None

    def failed_headless(*_args, **_kwargs):
        calls["headless"] += 1
        return None

    monkeypatch.setattr(image_pipeline, "fetch_response", exhausted)
    monkeypatch.setattr(image_pipeline, "_cancel_aware_sleep", lambda *_a: pytest.fail("outer loop slept"))
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(image_pipeline, "_fetch_headless", failed_headless)

    raw = json.dumps({"rows": [{"objects": [{"image": url} for url in urls]}]})
    image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    assert calls == {"http": 2, "headless": 1}


def test_failed_domain_skips_later_transport_calls(monkeypatch, tmp_path):
    urls = [
        "https://dead-cdn.example.test/one.jpg",
        "https://dead-cdn.example.test/two.jpg",
    ]
    calls = {"http": 0, "headless": 0}

    def exhausted(*_args, **_kwargs):
        calls["http"] += 1
        return None

    def failed_headless(*_args, **_kwargs):
        calls["headless"] += 1
        return None

    monkeypatch.setattr(image_pipeline, "fetch_response", exhausted)
    monkeypatch.setattr(image_pipeline, "_cancel_aware_sleep", lambda *_a: pytest.fail("outer loop slept"))
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", True)
    monkeypatch.setattr(image_pipeline, "_fetch_headless", failed_headless)

    raw = json.dumps({"rows": [{"objects": [{"image": url} for url in urls]}]})
    image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    assert calls == {"http": 1, "headless": 1}


def test_process_images_coalesces_relative_aliases(monkeypatch, tmp_path):
    calls = []
    response = FakeResponse(200, {"Content-Type": "image/png"}, b"same-image-bytes" * 8)

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return response

    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = json.dumps({"rows": [{"objects": [
        {"image": "./same.png"}, {"image": "same.png"},
    ]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    assert calls == ["https://example.test/game/same.png"]
    assert downloaded.count('"image":"images/same.png"') == 2
    assert not (tmp_path / "work" / "images" / "same_1.png").exists()


def test_process_images_flattens_external_image_paths_with_stable_names(monkeypatch, tmp_path):
    responses = {
        "https://cdn.example.test/original/05/8b/foo.jpg": FakeResponse(
            200, {"Content-Type": "image/jpeg"}, b"first-image" * 8
        ),
        "https://cdn.example.test/other/path/foo.jpg": FakeResponse(
            200, {"Content-Type": "image/jpeg"}, b"second-image" * 8
        ),
    }

    monkeypatch.setattr(image_pipeline, "fetch_response", lambda url, **_kwargs: responses[url])
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = json.dumps({"rows": [{"objects": [
        {"image": "https://cdn.example.test/original/05/8b/foo.jpg"},
        {"image": "https://cdn.example.test/other/path/foo.jpg"},
    ]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=2,
    )

    image_files = list((tmp_path / "work" / "images").iterdir())
    assert len(image_files) == 2
    assert all(item.is_file() for item in image_files)
    assert all(item.name.startswith("cdn_example_test_foo_") for item in image_files)
    assert all("original" not in item.parts and "other" not in item.parts for item in image_files)
    assert downloaded.count('"image":"images/cdn_example_test_foo_') == 2


def test_process_images_bounds_long_external_cdn_names_for_windows(monkeypatch, tmp_path):
    url = (
        "https://img.wattpad.com/1e6b077a9b612c4cb75d039ae98b3f78ba26cc26/"
        "68747470733a2f2f73332e616d617a6f6e6177732e636f6d2f776174747061642d"
        "6d656469612d736572766963652f53746f7279496d6167652f6475694d59735a"
        "694a5267394e413d3d2d313433323339313933312e3137626661363530393835"
        "34633866353737353530353939323136332e6a7067?s=fit&w=720&h=720"
    )
    response = FakeResponse(200, {"Content-Type": "image/jpeg"}, b"jpeg-bytes" * 8)
    monkeypatch.setattr(image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_success", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)

    raw = json.dumps({"rows": [{"objects": [{"image": url}]}]})
    _embed, downloaded, _resolved = image_pipeline.process_images(
        raw,
        "https://example.test/game/",
        download=True,
        temp_folder=str(tmp_path / "work"),
        max_workers=1,
    )

    files = list((tmp_path / "work" / "images").iterdir())
    assert len(files) == 1
    assert len(files[0].name) <= 140
    assert files[0].suffix == ".jpg"
    assert '"image":"images/' in downloaded


def test_image_content_dedup_is_scoped_per_output_folder(tmp_path):
    content = b"same-content"
    first_folder = tmp_path / "first" / "images"
    second_folder = tmp_path / "second" / "images"
    first_folder.mkdir(parents=True)
    second_folder.mkdir(parents=True)
    first_path = str(first_folder / "a.png")

    assert asset_scan._check_image_dedup(content, first_path, scope=str(first_folder)) is None
    assert asset_scan._check_image_dedup(
        content, str(first_folder / "b.png"), scope=str(first_folder),
    ) == first_path
    assert asset_scan._check_image_dedup(
        content, str(second_folder / "a.png"), scope=str(second_folder),
    ) is None


def test_font_aliases_fetch_once_and_same_name_different_bytes_are_preserved(monkeypatch, tmp_path):
    calls = []
    payloads = {
        "https://cdn.test/font.woff2?v=1": b"font-one",
        "https://other.test/font.woff2": b"font-two",
    }

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return FakeResponse(200, {"Content-Type": "font/woff2"}, payloads[url])

    monkeypatch.setattr(fonts, "fetch_response", fake_fetch)
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "font.woff2").write_bytes(b"pre-existing-font")
    project = json.dumps({
        "a": "https://cdn.test/font.woff2?v=1",
        "b": "https://cdn.test/font.woff2?v=2",
        "c": "https://other.test/font.woff2",
    })

    rewritten = fonts._download_fonts_into_folder(
        project, "https://example.test/game/", str(tmp_path),
    )

    assert sorted(calls) == [
        "https://cdn.test/font.woff2?v=1",
        "https://other.test/font.woff2",
    ]
    assert (fonts_dir / "font.woff2").read_bytes() == b"pre-existing-font"
    assert (fonts_dir / "font_1.woff2").read_bytes() == b"font-one"
    assert (fonts_dir / "font_2.woff2").read_bytes() == b"font-two"
    assert rewritten.count("fonts/font_1.woff2") == 2
    assert rewritten.count("fonts/font_2.woff2") == 1


def test_deep_scan_coalesces_cachebusters_but_keeps_query_variants(monkeypatch, tmp_path):
    (tmp_path / ".vite").mkdir()
    (tmp_path / "build").mkdir()
    for manifest in (".vite/manifest.json", "asset-manifest.json", "manifest.json", "build/asset-manifest.json"):
        path = tmp_path / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")

    candidates = {
        "https://example.test/game/foo.png?v=1",
        "https://example.test/game/foo.png?v=2",
        "https://example.test/game/foo.png?width=1",
        "https://example.test/game/foo.png?width=2",
    }
    calls = []

    def fake_fetch(url, **_kwargs):
        calls.append(url)
        return FakeResponse(200, {"Content-Type": "image/png"}, url.encode() * 2)

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_get_active_proxy", lambda: "")
    monkeypatch.setattr(image_pipeline, "_legacy", lambda: SimpleNamespace(_HTTP2_ENABLED=False))
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = image_pipeline._deep_scan_and_download_assets(
        str(tmp_path), "https://example.test/game/", str(tmp_path),
        max_workers=3, ai_mode="off",
    )

    assert calls.count("https://example.test/game/foo.png") == 1
    assert len(downloaded) == 3
    assert len(set(downloaded.values())) == 3
    assert all((tmp_path / rel).is_file() for rel in downloaded.values())


def test_deep_scan_keeps_valid_json_assets(monkeypatch, tmp_path):
    (tmp_path / "index.js").write_text("fetch('project.json')", encoding="utf-8")
    project_url = "https://example.test/game/project.json"
    payload = b'{"rows": [], "backpack": []}'

    def fake_fetch(url, **_kwargs):
        return FakeResponse(200, {"Content-Type": "application/json"}, payload)

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: {project_url})
    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_get_active_proxy", lambda: "")
    monkeypatch.setattr(image_pipeline, "_legacy", lambda: SimpleNamespace(_HTTP2_ENABLED=False))
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = image_pipeline._deep_scan_and_download_assets(
        str(tmp_path), "https://example.test/game/", str(tmp_path),
        max_workers=1, ai_mode="off",
    )

    assert downloaded == {project_url: "project.json"}
    assert (tmp_path / "project.json").read_bytes() == payload
    assert not image_pipeline._asset_is_error_document("application/json", payload, binary_asset=False)


def test_deep_scan_flattens_external_assets_into_type_folder(monkeypatch, tmp_path):
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")
    candidates = {
        "https://cdn.example.test/wordpress/wp-content/uploads/2024/01/hero.jpg",
        "https://other.example.test/736x/65/94/hero.jpg",
    }

    def fake_fetch(url, **_kwargs):
        return FakeResponse(200, {"Content-Type": "image/jpeg"}, b"jpg-bytes")

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_get_active_proxy", lambda: "")
    monkeypatch.setattr(image_pipeline, "_legacy", lambda: SimpleNamespace(_HTTP2_ENABLED=False))
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = image_pipeline._deep_scan_and_download_assets(
        str(tmp_path), "https://example.test/game/", str(tmp_path),
        max_workers=2, ai_mode="off",
    )

    assert len(downloaded) == 2
    assert all(rel.startswith("external/images/") for rel in downloaded.values())
    assert all(len(Path(rel).parts) == 3 for rel in downloaded.values())
    assert all((tmp_path / rel).is_file() for rel in downloaded.values())
    assert image_pipeline._deep_scan_external_rel_path(
        "https://cdn.example.test/avatarhd", b"\x00\x00\x00\x18ftypavif"
    ).startswith("external/images/avatarhd_")
    assert image_pipeline._deep_scan_external_rel_path(
        "https://cdn.example.test/avatarhd", b"\x00\x00\x00\x18ftypavif"
    ).endswith(".avif")


def test_deep_scan_never_saves_html_as_bin_and_detects_extensionless_images(monkeypatch, tmp_path):
    (tmp_path / "index.css").write_text("body{}", encoding="utf-8")
    image_url = "https://imagedelivery.example/avatarhd"
    html_urls = {
        "https://wormlewdmod.example/jjk/",
        "https://ghoulishghost.example/",
    }
    candidates = {image_url, *html_urls}
    avif = b"\x00\x00\x00\x18ftypavif" + b"payload"

    def fake_fetch(url, **_kwargs):
        if url == image_url:
            return FakeResponse(200, {"Content-Type": "image/avif"}, avif)
        return FakeResponse(200, {"Content-Type": "text/html"}, b"<!DOCTYPE html><title>Landing page</title>")

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: candidates)
    monkeypatch.setattr(image_pipeline, "fetch_response", fake_fetch)
    monkeypatch.setattr(image_pipeline, "_get_active_proxy", lambda: "")
    monkeypatch.setattr(image_pipeline, "_legacy", lambda: SimpleNamespace(_HTTP2_ENABLED=False))
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)

    downloaded = image_pipeline._deep_scan_and_download_assets(
        str(tmp_path), "https://example.test/game/", str(tmp_path),
        max_workers=2, ai_mode="off",
    )

    assert list(downloaded) == [image_url]
    assert downloaded[image_url].startswith("external/images/avatarhd_")
    assert downloaded[image_url].endswith(".avif")
    assert not list((tmp_path / "external" / "assets").glob("*.bin"))


def test_deep_scan_reports_missing_image_without_creating_local_placeholder(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    missing_url = "https://example.test/game/images/missing-card.jpg"
    events = []

    monkeypatch.setattr(image_pipeline, "run_asset_scanner_plugins", lambda *_a: {missing_url})
    monkeypatch.setattr(
        image_pipeline,
        "fetch_response",
        lambda url, **_kwargs: FakeResponse(404, {"Content-Type": "text/html"}, b"not found"),
    )
    monkeypatch.setattr(image_pipeline, "_get_active_proxy", lambda: "")
    monkeypatch.setattr(image_pipeline, "_legacy", lambda: SimpleNamespace(_HTTP2_ENABLED=False))
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_throttle_bandwidth", lambda *_a, **_k: None)
    monkeypatch.setattr(
        image_pipeline,
        "_emit_progress_event",
        lambda event_type, **payload: events.append({"type": event_type, **payload}),
    )

    downloaded = image_pipeline._deep_scan_and_download_assets(
        str(tmp_path), "https://example.test/game/", str(tmp_path),
        max_workers=1, ai_mode="off",
    )

    placeholder = tmp_path / "images" / "missing-card.jpg"
    assert downloaded == {}
    assert not placeholder.exists()
    assert events == [{
        "type": "file_failed",
        "name": "missing-card.jpg",
        "url": missing_url,
        "error": "HTTP 404",
    }]
    report = (tmp_path / "failed_assets.txt").read_text(encoding="utf-8")
    assert "HTTP 404" in report
    assert "local placeholder created" not in report


def test_media_extension_prefers_avif_signature_over_stale_mime():
    avif = b"\x00\x00\x00\x18ftypavif" + b"payload"
    assert image_pipeline._media_content_extension(
        "https://cdn.example/avatarhd", "image/jpeg", avif
    ) == ".avif"


def test_process_images_rejects_html_200_as_image(monkeypatch, tmp_path):
    response = FakeResponse(200, {"Content-Type": "text/html"}, b"<html>login page</html>")
    monkeypatch.setattr(image_pipeline, "fetch_response", lambda *_a, **_k: response)
    monkeypatch.setattr(image_pipeline, "_cache_get", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_cache_put", lambda *_a: None)
    monkeypatch.setattr(image_pipeline, "_domain_throttle", lambda _url: None)
    monkeypatch.setattr(image_pipeline, "_domain_record_failure", lambda *_a: 0)
    monkeypatch.setattr(image_pipeline, "_ssrf_block_cross_origin", lambda *_a: False)
    monkeypatch.setattr(image_pipeline, "_SELENIUM_ENABLED", False)
    monkeypatch.setattr(image_pipeline, "_is_gallery_dl_site", lambda _url: "")
    monkeypatch.setattr(image_pipeline, "_write_failed_images_log", lambda *_a, **_k: None)
    monkeypatch.setattr(image_pipeline, "write_asset_failure_summary", lambda *_a, **_k: None)
    raw = json.dumps({"rows": [{"objects": [{"image": "https://cdn.test/pic.png"}]}]})
    embedded, _downloaded, _failed = image_pipeline.process_images(
        raw, "https://public.test/game/", embed=True, output_dir=str(tmp_path), max_workers=1,
    )
    assert "data:text/html" not in embedded
    assert "https://cdn.test/pic.png" in embedded


def test_corrupt_cache_index_entries_become_cache_misses(tmp_path, monkeypatch):
    index = tmp_path / "index.json"
    index.write_text(json.dumps({
        "https://bad.test/a.png": 42,
        "https://bad.test/b.png": "not-a-sha256",
    }), encoding="utf-8")
    monkeypatch.setattr(cache_store, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache_store, "_CACHE_IDX", index)
    monkeypatch.setattr(cache_store, "_cache_index", {})
    monkeypatch.setattr(cache_store, "_cache_loaded", False)
    assert cache_store._cache_get("https://bad.test/a.png") is None
    assert cache_store._cache_get("https://bad.test/b.png") is None
    assert cache_store._cache_index == {}


def test_malformed_history_entries_are_filtered(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "bad": "entry",
        "https://good.test": {"last_downloaded": "2026-01-01T00:00:00"},
    }), encoding="utf-8")
    monkeypatch.setattr(history_store, "_HISTORY_FILE", str(path))
    assert history_store._load_history() == {
        "https://good.test": {"last_downloaded": "2026-01-01T00:00:00"}
    }


def test_batch_update_probe_closes_error_responses_and_skips_bad_history(monkeypatch):
    response = FakeResponse(404, {"Content-Length": "0"}, b"")
    monkeypatch.setattr(updates, "fetch_response", lambda *_a, **_k: response)
    results = updates._batch_check_updates({
        "bad": "entry",
        "https://good.test": {"success": True, "filename": "Good"},
    }, max_workers=0)
    assert results == [{
        "url": "https://good.test",
        "name": "Good",
        "status": "unreachable",
        "reason": "HTTP 404",
    }]
    assert response.closed


def test_offline_viewer_overlay_has_clean_unicode_labels():
    source = Path(injector.__file__).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    overlay = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "_CHEAT_OVERLAY" for target in node.targets):
            overlay = ast.literal_eval(node.value)
            break

    assert overlay is not None
    assert "♾️ Unlimited Choices" in overlay
    assert "☐ Deselect All Choices" in overlay
    assert not any(0x80 <= ord(char) <= 0x9F for char in overlay)


def test_safe_console_print_uses_readable_ascii_fallback():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii")
    _safe_console_print("source → asset — ✓", file=stream)
    stream.flush()

    assert raw.getvalue().replace(b"\r\n", b"\n") == b"source -> asset - OK\n"


@pytest.mark.parametrize("module_name", [
    "cyoa_downloader_app.download.image_pipeline",
    "cyoa_downloader_app.download.orchestrator",
    "cyoa_downloader_app.gui.panels",
])
def test_domain_modules_import_in_fresh_interpreter(module_name):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
