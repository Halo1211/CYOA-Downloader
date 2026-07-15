import ast
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.project import discover as discover_mod
from cyoa_downloader_app.download import website as website_mod

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code
        self.headers = {}


def _legacy_defined_functions():
    tree = ast.parse(LEGACY.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_phase17_live_discovery_helpers_are_real_module_exports():
    assert cyoa_downloader.get_source is discover_mod.get_source
    assert cyoa_downloader.url_file_exists is discover_mod.url_file_exists
    assert cyoa_downloader._parallel_head_check is discover_mod._parallel_head_check
    assert cyoa_downloader._normalize_auto_detect_output is discover_mod._normalize_auto_detect_output
    assert cyoa_downloader._auto_detect_output_variant is discover_mod._auto_detect_output_variant
    assert cyoa_downloader.auto_detect_mode is discover_mod.auto_detect_mode
    assert cyoa_downloader.auto_detect_modes_batch is discover_mod.auto_detect_modes_batch
    assert website_mod.get_source is discover_mod.get_source
    assert website_mod.url_file_exists is discover_mod.url_file_exists


def test_phase17_live_discovery_helpers_moved_out_of_legacy():
    names = _legacy_defined_functions()
    for name in {
        "get_source", "url_file_exists", "_parallel_head_check",
        "_normalize_auto_detect_output", "_auto_detect_output_variant",
        "auto_detect_mode", "auto_detect_modes_batch",
    }:
        assert name not in names


def test_get_source_decodes_response_content_with_project_parser(monkeypatch):
    def fake_fetch(url, **kwargs):
        assert url == "https://example.test/project.json"
        return FakeResponse("日本語".encode("utf-8"), 200)

    monkeypatch.setattr(discover_mod, "fetch_response", fake_fetch)

    assert discover_mod.get_source("https://example.test/project.json") == "日本語"


def test_url_file_exists_and_parallel_head_check_use_fetch_wrapper(monkeypatch):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        status = 200 if url.endswith("ok.json") else 404
        return FakeResponse(b"{}", status)

    monkeypatch.setattr(discover_mod, "fetch_response", fake_fetch)

    assert discover_mod.url_file_exists("https://example.test/ok.json") is True
    assert discover_mod.url_file_exists("https://example.test/missing.json") is False
    live = discover_mod._parallel_head_check([
        "https://example.test/ok.json",
        "https://example.test/missing.json",
    ], max_workers=99, timeout=1)
    assert live == ["https://example.test/ok.json"]
    assert all(call[1].get("stream") is True for call in calls)
    assert all(call[1].get("as_bytes") is not True for call in calls)


def test_auto_detect_mode_selects_cyoap_or_standard_without_network(monkeypatch):
    monkeypatch.setattr(discover_mod, "_load_settings", lambda: {"auto_detect_output": "zip"})

    from cyoa_downloader_app.project import cyoap_vue

    monkeypatch.setattr(cyoap_vue, "_probe_cyoap_vue_structure", lambda base, timeout=6: True)
    assert discover_mod.auto_detect_mode("https://example.test/game", timeout=1) == "cyoap_vue_zip"

    monkeypatch.setattr(cyoap_vue, "_probe_cyoap_vue_structure", lambda base, timeout=6: False)
    monkeypatch.setattr(discover_mod, "build_default_project_candidates", lambda url: [url + "/project.json"])
    monkeypatch.setattr(discover_mod, "_parallel_head_check", lambda candidates, **kwargs: candidates)
    assert discover_mod.auto_detect_mode("https://example.test/game", timeout=1) == "website_zip"
