import json

import cyoa_downloader
from cyoa_downloader_app.project import parse as parse_mod
from cyoa_downloader_app.project import discover as discover_mod
from cyoa_downloader_app.project import cyoap_vue as cyoap_mod
from cyoa_downloader_app.project import cyoa_cafe as cafe_mod


def test_phase4_facade_project_names_still_match_modules():
    assert cyoa_downloader.try_decode_bytes is parse_mod.try_decode_bytes
    assert cyoa_downloader.looks_like_project_payload is parse_mod.looks_like_project_payload
    assert cyoa_downloader.get_project_source is discover_mod.get_project_source
    assert cyoa_downloader.auto_detect_mode is discover_mod.auto_detect_mode
    assert cyoa_downloader.try_download_cyoap_vue_site is cyoap_mod.try_download_cyoap_vue_site
    assert cyoa_downloader.CYOACafeResolver is cafe_mod.CYOACafeResolver
    assert cyoa_downloader.get_iframe_url_from_cyoa_cafe is cafe_mod.get_iframe_url_from_cyoa_cafe


def test_phase4_parse_helpers_smoke():
    assert parse_mod.try_decode_bytes("héllo".encode("utf-8")) == "héllo"
    payload = json.dumps({"rows": [], "pointTypes": []})
    assert parse_mod.looks_like_project_payload(payload) is True
    assert parse_mod.extract_project_text_from_payload(payload) == '{"rows":[],"pointTypes":[]}'


def test_phase4_discovery_helpers_smoke():
    html = '<html><body><iframe src="https://viewer.example/app"></iframe><script>window.project={rows:[],pointTypes:[]}</script></body></html>'
    assert discover_mod.extract_iframe_urls(html) == ["https://viewer.example/app"]
    scripts = discover_mod.find_scripts(html)
    assert scripts == ["window.project={rows:[],pointTypes:[]}"]
    candidates = discover_mod.build_default_project_candidates("https://example.com/path/index.html")
    assert any(candidate.endswith("project.json") for candidate in candidates)


def test_phase4_cafe_url_classifier():
    assert cafe_mod._v462_is_cafe_url("https://cyoa.cafe/game/abc") is True
    assert cafe_mod._v466_is_cafe_metadata_game_url("https://cyoa.cafe/game/abc") is True
    assert cafe_mod._v466_is_cafe_metadata_game_url("https://example.com/game/abc") is False
