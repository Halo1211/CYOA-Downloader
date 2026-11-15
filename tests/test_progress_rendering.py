"""Progress presentation stays quiet when hidden and catches up on restore."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cyoa_downloader_app.core.progress import DownloadTelemetry
from cyoa_downloader_app.gui import final_behaviors
from cyoa_downloader_app.gui.progress_rendering import ProgressView, install_visibility


def fixture():
    callbacks, cancelled = {}, []
    root = SimpleNamespace(after=lambda ms, fn: callbacks.setdefault(len(callbacks), (ms, fn)) and len(callbacks) - 1,
                           after_cancel=cancelled.append)
    gui = SimpleNamespace(root=root, _v46_progress_expanded=True, _progress_window_mapped=True,
                          _v46_job_pb=Mock(), _v46_file_pb=Mock(), _v46_overall_pb=Mock(), _v46_job_var=Mock())
    return gui, callbacks, cancelled


def test_unchanged_values_do_not_cross_tk_bridge_and_new_widget_is_refreshed():
    gui, _callbacks, _cancelled = fixture()
    view = ProgressView(gui)
    for _ in range(30):
        view.set("_v46_job_var", "same")
        view.bar("_v46_overall_pb", .5)
    gui._v46_job_var.set.assert_called_once_with("same")
    gui._v46_overall_pb.set.assert_called_once_with(.5)
    gui._v46_overall_pb.configure.assert_called_once_with(mode="determinate")
    gui._v46_job_var = Mock()
    view.set("_v46_job_var", "same")
    gui._v46_job_var.set.assert_called_once_with("same")


def test_unknown_bars_share_one_paced_timer_without_native_internal_loops():
    gui, callbacks, _cancelled = fixture()
    view = ProgressView(gui)
    for _ in range(20):
        view.bar("_v46_job_pb", animate=True)
        view.bar("_v46_file_pb", animate=True)
    assert len(callbacks) == 1
    assert callbacks[0][0] == 80
    gui._v46_job_pb.start.assert_not_called()
    callbacks[0][1]()
    gui._v46_job_pb.step.assert_called_once()
    gui._v46_file_pb.step.assert_called_once()
    assert len(callbacks) == 2


@pytest.mark.parametrize("hidden", ["collapsed", "unmapped"])
def test_hiding_stops_animation_and_stale_callback_cannot_restart_it(hidden):
    gui, callbacks, cancelled = fixture()
    view = ProgressView(gui)
    view.bar("_v46_job_pb", animate=True)
    old = callbacks[0][1]
    if hidden == "collapsed":
        gui._v46_progress_expanded = False
    else:
        gui._progress_window_mapped = False
    view.halt()
    old()
    assert cancelled == [0]
    assert len(callbacks) == 1
    gui._v46_job_pb.step.assert_not_called()


def test_cancel_then_new_run_ignores_old_timer_and_retains_new_one():
    gui, callbacks, _cancelled = fixture()
    view = ProgressView(gui)
    view.bar("_v46_job_pb", animate=True)
    old = callbacks[0][1]
    view.bar("_v46_job_pb", 1)
    view.bar("_v46_job_pb", animate=True)
    current = view.after_id
    old()
    assert view.after_id == current
    gui._v46_job_pb.step.assert_not_called()
    callbacks[current][1]()
    gui._v46_job_pb.step.assert_called_once()


def test_window_visibility_uses_latest_telemetry_on_restore():
    gui, _callbacks, _cancelled = fixture()
    bindings, visible = {}, [True]
    gui.root.bind = lambda event, fn, **_kw: bindings.update({event: fn})
    gui.root.winfo_viewable = lambda: visible[0]
    gui._progress_view_ready = True
    gui._v46_telemetry = DownloadTelemetry()
    gui._v46_render_progress = Mock()
    install_visibility(gui)
    visible[0] = False
    bindings["<Unmap>"](SimpleNamespace(widget=gui.root))
    gui._v46_telemetry.apply({"type": "file_progress", "downloaded": 42, "total": 100})
    visible[0] = True
    bindings["<Map>"](SimpleNamespace(widget=gui.root))
    assert gui._v46_render_progress.call_args[0][0]["file_downloaded"] == 42


@pytest.mark.parametrize("state", ["IDLE", "COMPLETED", "FAILED", "CANCELLED", "DOWNLOADING"])
def test_hidden_details_never_start_or_update_detail_widgets(state):
    gui, callbacks, _cancelled = fixture()
    gui._v46_progress_expanded = False
    gui._language = "en"
    gui._v46_telemetry = DownloadTelemetry()
    gui._v46_telemetry.set_state(state)
    for name in ("_v46_overall_var", "_v46_state_var", "_v46_progress_summary_var", "_v46_copy_error_btn"):
        setattr(gui, name, Mock())
    final_behaviors._v46_render_progress(gui, gui._v46_telemetry.snapshot())
    gui._v46_job_var.set.assert_not_called()
    gui._v46_job_pb.configure.assert_not_called()
    gui._v46_file_pb.configure.assert_not_called()
    assert not callbacks
