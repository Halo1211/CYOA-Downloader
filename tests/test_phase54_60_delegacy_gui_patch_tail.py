import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEGACY = ROOT / "cyoa_downloader_app" / "runtime" / "surface.py"


def test_phase54_60_patch_bodies_left_legacy():
    source = LEGACY.read_text(encoding="utf-8")
    moved_defs = [
        "def _v25_ai_settings_panel(",
        "def _v25_manage_offline_viewers(",
        "def _v25_inject_into_viewer(",
        "def _v25_cloudflare_panel(",
        "def _v46_gui_init(",
        "def _v46_worker(",
        "def _v46_render_progress(",
        "def _v462_run_download(",
        "def _v463_rebuild_progress_workspace(",
        "def _v466_run_download(",
        "def _record_history(",
    ]
    for needle in moved_defs:
        assert needle not in source


def test_phase54_60_modules_export_moved_patch_helpers():
    from cyoa_downloader_app.gui import final_behaviors, final_behaviors, final_behaviors, final_behaviors, final_behaviors
    from cyoa_downloader_app.storage import history

    assert callable(final_behaviors._v25_cloudflare_panel)
    assert callable(final_behaviors._v46_worker)
    assert callable(final_behaviors._v462_run_download)
    assert callable(final_behaviors._v463_rebuild_progress_workspace)
    assert callable(final_behaviors._v466_run_download)
    assert callable(history._record_history)


def test_phase54_60_legacy_is_now_mostly_shim():
    source = LEGACY.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 1800


