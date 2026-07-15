import ast
import io
import json
import zipfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.project import parse as parse_mod

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"
PARSE = ROOT / "cyoa_downloader_app" / "project" / "parse.py"


def _legacy_defined_symbols():
    tree = ast.parse(LEGACY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_phase14_parse_module_no_longer_bridges_to_legacy():
    source = PARSE.read_text(encoding="utf-8")
    assert "from .. import legacy" not in source
    assert "_legacy." not in source


def test_phase14_project_parser_helpers_moved_out_of_legacy():
    assert cyoa_downloader.try_decode_bytes is parse_mod.try_decode_bytes
    assert cyoa_downloader.is_zip_bytes is parse_mod.is_zip_bytes
    assert cyoa_downloader.looks_like_project_object is parse_mod.looks_like_project_object
    assert cyoa_downloader.looks_like_project_payload is parse_mod.looks_like_project_payload
    assert cyoa_downloader.extract_project_text_from_payload is parse_mod.extract_project_text_from_payload
    assert cyoa_downloader.extract_project_from_archive_bytes is parse_mod.extract_project_from_archive_bytes

    names = _legacy_defined_symbols()
    for name in {
        "try_decode_bytes", "is_zip_bytes", "looks_like_project_object",
        "looks_like_project_payload", "extract_balanced_brace_block",
        "extract_embedded_project_from_js", "extract_project_from_archive_bytes",
        "parse_jsonish_text", "normalize_project_payload_text",
        "extract_project_text_from_payload", "extract_json_like_block",
        "_extract_website_from_archive_zip_name",
    }:
        assert name not in names


def test_phase14_parse_jsonish_and_embedded_project_smoke():
    payload = '{rows:[], pointTypes:[], image:"cover.png"}'
    parsed = parse_mod.parse_jsonish_text(payload)
    assert parsed["rows"] == []
    assert parsed["image"] == "cover.png"

    js = "window.__APP__=" + json.dumps({"rows": [], "pointTypes": [], "image": "cover.png"}) + ";"
    embedded = parse_mod.extract_embedded_project_from_js(js)
    assert embedded
    assert parse_mod.extract_project_text_from_payload(js) == '{"rows":[],"pointTypes":[],"image":"cover.png"}'


def test_phase14_extracts_project_from_reactive_app_wrapper():
    payload = {
        "version": "2.9.3",
        "rows": [{"title": "Intro", "objects": []}],
        "backpack": [],
        "pointTypes": [],
    }
    js = "const app=i(" + json.dumps(payload) + ");"
    embedded = parse_mod.extract_embedded_project_from_js(js)
    assert embedded == json.dumps(payload)


def test_phase14_extract_project_from_archive_bytes_smoke():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("docs/readme.txt", "not a project")
        zf.writestr("project.json", json.dumps({"rows": [], "pointTypes": [], "image": "cover.png"}))
    extracted = parse_mod.extract_project_from_archive_bytes(raw.getvalue(), "https://example.test/project.zip")
    assert extracted == '{"rows":[],"pointTypes":[],"image":"cover.png"}'
