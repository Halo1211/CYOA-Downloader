import os
from contextlib import contextmanager
from pathlib import Path

import cyoa_downloader as facade
import pytest

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


def test_base_run_download_releases_output_lease_after_failure(tmp_path, monkeypatch):
    events = []
    starting_dir = os.getcwd()
    orchestrator._sync_legacy_globals()

    @contextmanager
    def fake_output_lease(output_dir):
        events.append(("enter", os.path.realpath(output_dir)))
        try:
            yield os.path.realpath(output_dir)
        finally:
            events.append(("exit", os.path.realpath(output_dir)))

    # Refresh once as production does, then keep the moved implementation from
    # replacing the injected failure/lease probes on its second refresh.
    monkeypatch.setattr(orchestrator, "_sync_legacy_globals", lambda: None)
    monkeypatch.setattr(orchestrator, "_set_last_preview_folder", lambda _value: None)
    monkeypatch.setattr(orchestrator, "output_directory_lease", fake_output_lease)
    monkeypatch.setattr(
        orchestrator,
        "get_project_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )

    with pytest.raises(RuntimeError, match="forced failure"):
        orchestrator._base_run_download(
            "https://example.test/cyoa/",
            file_name="lease-test",
            output_dir=str(tmp_path),
            ai_provider="openai",
            ai_mode="off",
        )

    canonical = os.path.realpath(str(tmp_path))
    assert events == [("enter", canonical), ("exit", canonical)]
    assert os.getcwd() == starting_dir
