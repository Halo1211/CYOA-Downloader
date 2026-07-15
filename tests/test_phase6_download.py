import tempfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.download import fonts as fonts_mod
from cyoa_downloader_app.download import image_pipeline as image_mod
from cyoa_downloader_app.download import orchestrator as orchestrator_mod
from cyoa_downloader_app.download import package as package_mod
from cyoa_downloader_app.download import website as website_mod


def test_phase6_facade_download_names_still_match_modules():
    assert cyoa_downloader.run_download is orchestrator_mod.run_download
    assert cyoa_downloader.process_images is image_mod.process_images
    assert cyoa_downloader._deep_scan_project_assets is image_mod._deep_scan_project_assets
    assert cyoa_downloader.analyse_fonts is fonts_mod.analyse_fonts
    assert cyoa_downloader.WebsiteDownloader is website_mod.WebsiteDownloader
    assert cyoa_downloader.write_package_manifest is package_mod.write_package_manifest
    assert cyoa_downloader.verify_output_package is package_mod.verify_output_package
    assert cyoa_downloader.prepare_clean_output_folder is package_mod.prepare_clean_output_folder


def test_phase6_pipeline_helpers_smoke():
    svg = image_mod._make_placeholder_svg("missing.png")
    assert isinstance(svg, (bytes, bytearray))
    assert b"<svg" in svg

    assert fonts_mod._find_font_urls("", "https://example.com/", "", []) == {}
    assert website_mod.is_zip_bytes(b"PK\x03\x04xxxx") is True
    assert package_mod.clean_url_path_component("a%20b/c?d") == "a b_c_d"
    assert package_mod.canonicalize_url("HTTPS://Example.COM:443/a/../b?x=1#frag") == "https://example.com/b?x=1"


def test_phase6_package_manifest_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "index.html").write_text("<html></html>", encoding="utf-8")
        ok, msg = package_mod.write_package_manifest(str(root))
        assert ok, msg
        assert (root / "cyoa_manifest.json").exists()
        verify_ok, report = package_mod.verify_output_package(str(root))
        assert isinstance(verify_ok, bool)
        assert isinstance(report, str)
        assert "package verification" in report
