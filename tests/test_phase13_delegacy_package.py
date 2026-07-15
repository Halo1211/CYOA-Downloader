import ast
import os
import tempfile
import zipfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.download import package as package_mod

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


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


def test_package_zip_file_helpers_moved_out_of_legacy():
    assert cyoa_downloader.save_string_to_file is package_mod.save_string_to_file
    assert cyoa_downloader.zip_temp_folder is package_mod.zip_temp_folder
    assert cyoa_downloader.atomic_stream_response_to_file is package_mod.atomic_stream_response_to_file
    assert cyoa_downloader._finalize_site_folder is package_mod._finalize_site_folder

    names = _legacy_defined_symbols()
    for name in {"save_string_to_file", "zip_temp_folder", "atomic_stream_response_to_file", "_finalize_site_folder"}:
        assert name not in names


def test_package_manifest_helpers_moved_out_of_legacy():
    assert cyoa_downloader.write_package_manifest is package_mod.write_package_manifest
    assert cyoa_downloader.verify_output_package is package_mod.verify_output_package
    assert cyoa_downloader._hash_file_sha256 is package_mod._hash_file_sha256
    assert cyoa_downloader._walk_package_files is package_mod._walk_package_files
    assert cyoa_downloader._load_package_manifest is package_mod._load_package_manifest

    names = _legacy_defined_symbols()
    for name in {"write_package_manifest", "verify_output_package", "_hash_file_sha256", "_walk_package_files", "_load_package_manifest"}:
        assert name not in names


def test_output_name_temp_helpers_moved_out_of_legacy():
    assert cyoa_downloader.clean_url_path_component is package_mod.clean_url_path_component
    assert cyoa_downloader._build_output_name is package_mod._build_output_name
    assert cyoa_downloader.get_first_subdomain is package_mod.get_first_subdomain
    assert cyoa_downloader.create_random_temp_folder is package_mod.create_random_temp_folder
    assert cyoa_downloader.delete_temp_folder is package_mod.delete_temp_folder

    names = _legacy_defined_symbols()
    for name in {"clean_url_path_component", "_build_output_name", "get_first_subdomain", "create_random_temp_folder", "delete_temp_folder"}:
        assert name not in names


def test_phase13_zip_temp_folder_uses_normalized_members():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
        src = Path(tmp) / "src"
        nested = src / "images"
        nested.mkdir(parents=True)
        (nested / "a.png").write_bytes(b"png")
        old = os.getcwd()
        try:
            os.chdir(cwd)
            out = package_mod.zip_temp_folder(str(src), "unit_archive")
        finally:
            os.chdir(old)
        assert Path(out).exists()
        with zipfile.ZipFile(out) as zf:
            assert "images/a.png" in zf.namelist()
