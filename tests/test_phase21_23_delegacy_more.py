from pathlib import Path

import cyoa_downloader as facade
from cyoa_downloader_app.download import audio_download
from cyoa_downloader_app.runtime import surface as legacy
from cyoa_downloader_app.integrations import plugins
from cyoa_downloader_app.diagnostics import updates


def test_phase21_plugin_registry_is_real_module_and_shared():
    legacy_text = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "class _PluginRegistry:" not in legacy_text
    assert "def run_asset_scanner_plugins" not in legacy_text
    assert facade._ASSET_SCANNER_PLUGINS is plugins._ASSET_SCANNER_PLUGINS

    plugins.register_asset_scanner("phase21-test", lambda text, file_url, base_url, ext: {base_url + "/x.png"})
    try:
        assert plugins.run_asset_scanner_plugins("", "https://a/app.js", "https://a", ".js") == {"https://a/x.png"}
    finally:
        plugins._ASSET_SCANNER_PLUGINS.unregister("phase21-test")


def test_phase22_update_helpers_are_real_module_exports():
    legacy_text = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "def _check_for_app_updates" not in legacy_text
    assert "def _batch_check_updates" not in legacy_text
    assert facade._check_for_app_updates is updates._check_for_app_updates
    assert facade._batch_check_updates is updates._batch_check_updates


def test_phase23_ytdlp_hook_reads_legacy_callback():
    legacy_text = Path("cyoa_downloader_app/runtime/surface.py").read_text(encoding="utf-8")
    assert "def _make_ytdlp_hook" not in legacy_text
    assert "def _download_youtube_audio" not in legacy_text
    assert facade._make_ytdlp_hook is audio_download._make_ytdlp_hook

    seen = []
    legacy._ytdlp_gui_progress_cb = lambda vid, idx, total, pct, speed: seen.append((vid, idx, total, pct, speed))
    try:
        hook = audio_download._make_ytdlp_hook("dQw4w9WgXcQ", 2, 5)
        hook({"status": "downloading", "_percent_str": " 50% ", "_speed_str": " 1MiB/s ", "_eta_str": " 10s "})
    finally:
        legacy._ytdlp_gui_progress_cb = None
    assert seen == [("dQw4w9WgXcQ", 2, 5, "50%", "1MiB/s")]
