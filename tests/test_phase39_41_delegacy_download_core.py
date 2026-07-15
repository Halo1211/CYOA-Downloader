from pathlib import Path

import cyoa_downloader as facade
from cyoa_downloader_app.download import image_pipeline, orchestrator


def test_phase39_process_images_is_real_module():
    legacy_text = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "Download image AND audio assets referenced" not in legacy_text
    assert image_pipeline.process_images.__module__ == "cyoa_downloader_app.download.image_pipeline"
    assert facade.process_images is image_pipeline.process_images


def test_phase40_deep_scan_downloader_is_real_module():
    legacy_text = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "def _deep_scan_and_download_assets(" not in legacy_text
    assert image_pipeline._deep_scan_and_download_assets.__module__ == "cyoa_downloader_app.download.image_pipeline"
    assert facade._deep_scan_and_download_assets is image_pipeline._deep_scan_and_download_assets


def test_phase41_base_run_download_moved_but_public_wrapper_preserved():
    legacy_text = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "Main download orchestrator." not in legacy_text
    assert orchestrator._base_run_download.__module__ == "cyoa_downloader_app.download.orchestrator"
    # Public run_download remains the final historical wrapper surface.
    assert callable(facade.run_download)
    assert callable(orchestrator.run_download)
