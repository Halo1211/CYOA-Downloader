from __future__ import annotations

import inspect

from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.gui.app import CYOADownloaderGUI
from cyoa_downloader_app.gui.final_behaviors import _v463_progress_detail_height


def test_javascript_archive_policy_is_exposed_in_settings_center():
    source = inspect.getsource(CYOADownloaderGUI._settings_maintenance_panel)

    assert "JavaScript Archive Policy" in source
    assert "Kebijakan Arsip JavaScript" in source
    assert "archive_runtime_max_pages" in source
    assert "archive_settle_time_ms" in source
    assert "archive_no_progress_rounds" in source
    assert "Every number is a safety cap" in source
    assert "Semua angka adalah batas pengaman" in source
    assert 'self._show_feature_guide("settings")' in source


def test_legacy_feature_archive_card_is_hidden_to_avoid_duplicate_controls():
    source = inspect.getsource(CYOADownloaderGUI._toggles_panel)
    assert "archive_card.grid_remove()" in source


def test_expanded_progress_restores_main_panels_instead_of_focus_takeover():
    source = inspect.getsource(final_behaviors._v463_apply_progress_visibility)
    assert "panel.grid()" in source
    assert "focus_mode" not in source
    assert "_v463_set_queue_density" in source


def test_progress_detail_frame_uses_one_compact_content_height():
    assert _v463_progress_detail_height(False, 1080, 400) == 0
    assert _v463_progress_detail_height(True, 800, 400) == 148
    assert _v463_progress_detail_height(True, 1440, 700) == 148


def test_final_gui_labels_do_not_contain_utf8_mojibake():
    source = inspect.getsource(final_behaviors)
    for broken_prefix in ("Ã", "â", "Â", "ð"):
        assert broken_prefix not in source
    assert " — " in source
    assert "…" in source
