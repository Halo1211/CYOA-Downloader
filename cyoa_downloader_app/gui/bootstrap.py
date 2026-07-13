"""Final GUI behavior bootstrap.

Historical GUI behavior bodies live in ``gui.final_behaviors`` for
compatibility, while this module owns the ordered captures and method bindings
that compose the exported GUI class.
"""
from __future__ import annotations

from typing import Any, Iterable, MutableMapping, Tuple
import threading
from ..runtime import state as _runtime_state


def _sync(module_sync: Any, namespace: MutableMapping[str, Any]) -> None:
    module_sync(dict(namespace))


MethodBinding = Tuple[str, Any]


def _bind_methods(
    cls: type,
    bindings: Iterable[MethodBinding],
    *,
    logger: Any = None,
    phase: str = "",
    recoverable: bool = False,
) -> None:
    """Attach a batch of final GUI methods through one explicit binding point."""
    for name, value in bindings:
        try:
            setattr(cls, name, value)
        except Exception as exc:
            if not recoverable:
                raise
            if logger is not None:
                logger.debug("Ignored recoverable exception applying %s GUI binding %s: %s", phase, name, exc)


def _resync_modules(sync_fns: Iterable[Any], namespace: MutableMapping[str, Any], logger: Any, context: str) -> None:
    """Mirror the compatibility namespace into moved GUI modules."""
    snapshot = dict(namespace)
    for sync_fn in sync_fns:
        try:
            sync_fn(snapshot)
        except Exception as exc:
            if logger is not None:
                try:
                    logger.debug("Ignored recoverable exception during %s GUI namespace sync: %s", context, exc)
                except Exception:
                    pass


def bootstrap_gui_runtime(namespace: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Apply the historical GUI patch stack using *namespace* as legacy state.

    The order intentionally mirrors the old single-file script. The function
    mutates and returns *namespace* so ``runtime.surface`` can remain a thin
    compatibility export while old private names stay available.
    """
    logger = namespace["logger"]

    from .app import CYOADownloaderGUI, launch_gui, _gui_exists, _sync_legacy_globals as _gui_app_sync_legacy_globals
    namespace.update({
        "CYOADownloaderGUI": CYOADownloaderGUI,
        "launch_gui": launch_gui,
        "_gui_exists": _gui_exists,
        "_gui_app_sync_legacy_globals": _gui_app_sync_legacy_globals,
    })
    _gui_app_sync_legacy_globals(dict(namespace))
    namespace["_APP_FINAL_SHOW_RESULTS"] = CYOADownloaderGUI._show_results
    namespace["_APP_FINAL_BATCH_UPDATE_PANEL"] = CYOADownloaderGUI._batch_update_panel
    namespace["_APP_FINAL_DIAGNOSTICS_PANEL"] = CYOADownloaderGUI._diagnostics_panel
    namespace["_APP_FINAL_ADD_URL_TO_QUEUE"] = CYOADownloaderGUI._add_url_to_queue
    namespace["_APP_FINAL_AI_SETTINGS_PANEL"] = CYOADownloaderGUI._ai_settings_panel
    namespace["_APP_FINAL_MANAGE_OFFLINE_VIEWERS"] = CYOADownloaderGUI._manage_offline_viewers
    namespace["_APP_FINAL_CLOUDFLARE_PANEL"] = CYOADownloaderGUI._cloudflare_panel
    namespace["_APP_FINAL_CACHE_MANAGER_PANEL"] = CYOADownloaderGUI._cache_manager_panel
    namespace["_APP_FINAL_CHECK_UPDATES_PANEL"] = CYOADownloaderGUI._check_updates_panel

    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v24_card, _v24_badge, _v24_show_results,
        _v24_batch_update_panel, _v24_diagnostics_panel, _v24_add_url_to_queue,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    _bind_methods(CYOADownloaderGUI, (
        ("_show_results", namespace["_APP_FINAL_SHOW_RESULTS"]),
        ("_batch_update_panel", namespace["_APP_FINAL_BATCH_UPDATE_PANEL"]),
        ("_diagnostics_panel", namespace["_APP_FINAL_DIAGNOSTICS_PANEL"]),
        ("_add_url_to_queue", namespace["_APP_FINAL_ADD_URL_TO_QUEUE"]),
    ), logger=logger, phase="v24", recoverable=True)

    from .widgets import (
        _v25_safe_after, _v25_safe_after_widget, _v25_center_window,
        _v27_ai_provider_values, _v27_safe_after, _v27_open_path,
    )
    namespace.update({
        "_v25_safe_after": _v25_safe_after,
        "_v25_safe_after_widget": _v25_safe_after_widget,
        "_v25_center_window": _v25_center_window,
        "_v27_ai_provider_values": _v27_ai_provider_values,
        "_v27_safe_after": _v27_safe_after,
        "_v27_open_path": _v27_open_path,
    })

    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v25_ai_settings_panel, _v25_manage_offline_viewers,
        _v25_inject_into_viewer, _v25_cloudflare_panel,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    _bind_methods(CYOADownloaderGUI, (
        ("_ai_settings_panel", namespace["_APP_FINAL_AI_SETTINGS_PANEL"]),
        ("_manage_offline_viewers", namespace["_APP_FINAL_MANAGE_OFFLINE_VIEWERS"]),
        ("_cloudflare_panel", namespace["_APP_FINAL_CLOUDFLARE_PANEL"]),
    ), logger=logger, phase="v25", recoverable=True)

    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v27_cache_manager_panel, _v27_check_updates_panel, _v27_ai_settings_panel,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    _bind_methods(CYOADownloaderGUI, (
        ("_cache_manager_panel", namespace["_APP_FINAL_CACHE_MANAGER_PANEL"]),
        ("_check_updates_panel", namespace["_APP_FINAL_CHECK_UPDATES_PANEL"]),
        ("_ai_settings_panel", namespace["_APP_FINAL_AI_SETTINGS_PANEL"]),
    ), logger=logger, phase="v27", recoverable=True)

    from ..core.progress import (
        DownloadCancelledError, DownloadState, DownloadTelemetry, _V46_STAGE_BANDS,
        format_bytes, format_speed, format_duration,
        calculate_smoothed_speed, calculate_eta, calculate_stage_progress,
    )
    from ..core.url_utils import truncate_display_url, canonicalize_url
    from ..core.archive import validate_zip_archive
    from .telemetry_log import _V46TelemetryLogHandler
    from ..project.cyoa_cafe import CYOACafeResolutionError, CYOACafeResolver, get_iframe_url_from_cyoa_cafe
    namespace.update(locals())

    namespace["_APP_FINAL_INIT"] = CYOADownloaderGUI.__init__
    namespace["_APP_FINAL_SETUP_UI"] = CYOADownloaderGUI._setup_ui
    namespace["_APP_FINAL_V46_ENQUEUE_PROGRESS"] = CYOADownloaderGUI._v46_enqueue_progress
    namespace["_APP_FINAL_V46_SET_EVENT_SINK"] = CYOADownloaderGUI._v46_set_event_sink
    namespace["_APP_FINAL_V46_APPLY_PROGRESS_VISIBILITY"] = CYOADownloaderGUI._v46_apply_progress_visibility
    namespace["_APP_FINAL_V46_TOGGLE_PROGRESS_PANEL"] = CYOADownloaderGUI._v46_toggle_progress_panel
    namespace["_APP_FINAL_V46_INSTALL_URL_MENU"] = CYOADownloaderGUI._v46_install_url_menu
    namespace["_APP_FINAL_V462_REFRESH_RESPONSIVE_LAYOUT"] = CYOADownloaderGUI._v462_refresh_responsive_layout
    namespace["_APP_FINAL_V463_ARRANGE_PROGRESS_AND_LOG"] = CYOADownloaderGUI._v463_arrange_progress_and_log
    namespace["_APP_FINAL_V463_REBUILD_PROGRESS_WORKSPACE"] = CYOADownloaderGUI._v463_rebuild_progress_workspace
    namespace["_APP_FINAL_START"] = CYOADownloaderGUI._start
    namespace["_APP_FINAL_WORKER"] = CYOADownloaderGUI._worker
    namespace["_APP_FINAL_DONE"] = CYOADownloaderGUI._done
    namespace["_APP_FINAL_V46_CANCEL"] = CYOADownloaderGUI._v46_cancel
    namespace["_APP_FINAL_V46_ON_CLOSE"] = CYOADownloaderGUI._v46_on_close
    namespace["_APP_FINAL_V46_FINISH_CLOSE"] = CYOADownloaderGUI._v46_finish_close
    namespace["_APP_FINAL_V46_COPY_ERROR"] = CYOADownloaderGUI._v46_copy_error
    namespace["_APP_FINAL_RECORD_SPEED_BYTES"] = CYOADownloaderGUI._record_speed_bytes
    namespace["_APP_FINAL_ON_YTDLP_PROGRESS"] = CYOADownloaderGUI._on_ytdlp_progress
    namespace["_APP_FINAL_START_SPEED_GRAPH"] = CYOADownloaderGUI._start_speed_graph
    namespace["_APP_FINAL_STOP_SPEED_GRAPH"] = CYOADownloaderGUI._stop_speed_graph
    namespace["_APP_FINAL_V46_POLL_PROGRESS"] = CYOADownloaderGUI._v46_poll_progress
    namespace["_APP_FINAL_V46_RENDER_PROGRESS"] = CYOADownloaderGUI._v46_render_progress
    namespace["_APP_FINAL_V46_DRAW_SPEED_GRAPH"] = CYOADownloaderGUI._v46_draw_speed_graph
    namespace["_v46_gui_init_legacy"] = CYOADownloaderGUI._init_base
    namespace["_v46_gui_setup_ui_legacy"] = CYOADownloaderGUI._setup_ui_base
    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v46_gui_init, _v46_default_progress_expanded,
        _v46_apply_progress_visibility, _v46_toggle_progress_panel,
        _v46_gui_setup_ui, _v46_install_url_menu, _v46_enqueue_progress,
        _v46_set_event_sink, _v46_start, _v46_worker, _v46_done,
        _v46_cancel, _v46_on_close, _v46_finish_close, _v46_copy_error,
        _v46_record_speed_bytes, _v46_on_ytdlp_progress,
        _v46_start_speed_graph, _v46_stop_speed_graph, _v46_poll_progress,
        _v46_render_progress, _v46_draw_speed_graph,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    _bind_methods(CYOADownloaderGUI, (
        ("__init__", namespace["_APP_FINAL_INIT"]),
        ("_setup_ui", _v46_gui_setup_ui),
        ("_v46_apply_progress_visibility", _v46_apply_progress_visibility),
        ("_v46_toggle_progress_panel", _v46_toggle_progress_panel),
        ("_v46_install_url_menu", _v46_install_url_menu),
        ("_v46_enqueue_progress", namespace["_APP_FINAL_V46_ENQUEUE_PROGRESS"]),
        ("_v46_set_event_sink", namespace["_APP_FINAL_V46_SET_EVENT_SINK"]),
        ("_start", namespace["_APP_FINAL_START"]),
        ("_worker", namespace["_APP_FINAL_WORKER"]),
        ("_done", namespace["_APP_FINAL_DONE"]),
        ("_v46_cancel", namespace["_APP_FINAL_V46_CANCEL"]),
        ("_v46_on_close", namespace["_APP_FINAL_V46_ON_CLOSE"]),
        ("_v46_finish_close", namespace["_APP_FINAL_V46_FINISH_CLOSE"]),
        ("_v46_copy_error", namespace["_APP_FINAL_V46_COPY_ERROR"]),
        ("_record_speed_bytes", namespace["_APP_FINAL_RECORD_SPEED_BYTES"]),
        ("_on_ytdlp_progress", namespace["_APP_FINAL_ON_YTDLP_PROGRESS"]),
        ("_start_speed_graph", namespace["_APP_FINAL_START_SPEED_GRAPH"]),
        ("_stop_speed_graph", namespace["_APP_FINAL_STOP_SPEED_GRAPH"]),
        ("_v46_poll_progress", namespace["_APP_FINAL_V46_POLL_PROGRESS"]),
        ("_v46_render_progress", namespace["_APP_FINAL_V46_RENDER_PROGRESS"]),
        ("_v46_draw_speed_graph", namespace["_APP_FINAL_V46_DRAW_SPEED_GRAPH"]),
    ), logger=logger, phase="v46")

    namespace.update({
        "_CYOA_CAFE_PURE_CACHE": {},
        "_CYOA_CAFE_PURE_CACHE_LOCK": threading.RLock(),
        "_CYOA_CAFE_RESOLUTION_KIND": {},
        "_CYOA_CAFE_RESOLUTION_KIND_LOCK": threading.RLock(),
        "_V461_CAFE_RESOLVE": CYOACafeResolver.resolve,
        "_V461_CAFE_INVALIDATE": CYOACafeResolver.invalidate,
        "_V461_AUTO_DETECT_MODE": namespace["auto_detect_mode"],
        "_V461_AUTO_DETECT_OUTPUT_VARIANT": namespace["_auto_detect_output_variant"],
        "_V461_RUN_DOWNLOAD": namespace["run_download"],
    })
    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v462_default_cafe_fetch, _v462_resolution_key,
        _v462_record_resolution_kind, _v462_get_resolution_kind,
        _v462_pure_cache_get, _v462_pure_cache_put, _v462_invalidate_cafe_cache,
        _v462_authoritative_pure_method, _v462_validate_pure_website_candidate,
        _v462_resolve_cafe, _v462_auto_detect_output_variant,
        _v462_auto_detect_mode, _v462_is_cafe_url,
        _v462_resolve_pure_download_url, _v462_run_download,
        _v462_default_progress_expanded, _v462_compact_queue_height,
        _v462_find_main_panels, _v462_configure_queue_viewport,
        _v462_apply_progress_visibility_gui, _v462_refresh_responsive_layout,
        _v462_gui_setup_ui_final,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    CYOACafeResolver._default_fetch = staticmethod(_v462_default_cafe_fetch)  # type: ignore[assignment]
    CYOACafeResolver.invalidate = staticmethod(_v462_invalidate_cafe_cache)  # type: ignore[assignment]
    CYOACafeResolver.validate_pure_website_candidate = _v462_validate_pure_website_candidate  # type: ignore[attr-defined]
    CYOACafeResolver.resolve = _v462_resolve_cafe  # type: ignore[assignment]
    namespace["_auto_detect_output_variant"] = _v462_auto_detect_output_variant
    namespace["auto_detect_mode"] = _v462_auto_detect_mode
    namespace["run_download"] = _v462_run_download
    namespace["_v46_default_progress_expanded"] = _v462_default_progress_expanded
    namespace["_V461_GUI_SETUP_UI_FINAL"] = CYOADownloaderGUI._setup_ui
    namespace["_V461_APPLY_PROGRESS_VISIBILITY_FINAL"] = CYOADownloaderGUI._v46_apply_progress_visibility
    _bind_methods(CYOADownloaderGUI, (
        ("_setup_ui", _v462_gui_setup_ui_final),
        ("_v46_apply_progress_visibility", _v462_apply_progress_visibility_gui),
        ("_v462_refresh_responsive_layout", _v462_refresh_responsive_layout),
    ), logger=logger, phase="v462")

    namespace["_V462_GUI_SETUP_UI_FOR_V463"] = CYOADownloaderGUI._setup_ui
    namespace["_V462_APPLY_PROGRESS_VISIBILITY_FOR_V463"] = CYOADownloaderGUI._v46_apply_progress_visibility
    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _V469_STATE_LABELS_ID, _V469_PROGRESS_STRINGS,
        _v463_arrange_progress_and_log, _v463_apply_progress_visibility,
        _v469_lang, _v469_ps, _v469_state_label,
        _v463_rebuild_progress_workspace, _v463_gui_setup_ui_final,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    _bind_methods(CYOADownloaderGUI, (
        ("_setup_ui", _v463_gui_setup_ui_final),
        ("_v46_apply_progress_visibility", namespace["_APP_FINAL_V46_APPLY_PROGRESS_VISIBILITY"]),
        ("_v46_toggle_progress_panel", namespace["_APP_FINAL_V46_TOGGLE_PROGRESS_PANEL"]),
        ("_v46_install_url_menu", namespace["_APP_FINAL_V46_INSTALL_URL_MENU"]),
        ("_v462_refresh_responsive_layout", namespace["_APP_FINAL_V462_REFRESH_RESPONSIVE_LAYOUT"]),
        ("_v463_arrange_progress_and_log", namespace["_APP_FINAL_V463_ARRANGE_PROGRESS_AND_LOG"]),
        ("_v463_rebuild_progress_workspace", namespace["_APP_FINAL_V463_REBUILD_PROGRESS_WORKSPACE"]),
    ), logger=logger, phase="v463")

    namespace["_APP_DISPLAY_NAME"] = "CYOA Downloader"
    namespace["_v465_session_init_lock"] = _runtime_state._v465_session_init_lock
    from ..storage.history import _record_history, _v465_history_lock
    from .logging_ui import (
        GUILogHandler, _v465_configure_log_tags, _v465_log_tag,
        _v465_setup_logging, _v465_insert_log_line, _v465_poll_log, _v465_safe_message,
    )
    namespace.update(locals())
    namespace["_V465_PREVIOUS_APPLY_THEME"] = CYOADownloaderGUI._apply_theme_base
    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v465_apply_theme,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    _bind_methods(CYOADownloaderGUI, (
        ("_setup_logging", _v465_setup_logging),
        ("_poll_log", _v465_poll_log),
        ("_safe_message", _v465_safe_message),
    ), logger=logger, phase="v465")

    namespace["_V466_PREVIOUS_RUN_DOWNLOAD"] = namespace["run_download"]
    namespace["_V466_PREVIOUS_SETUP_UI"] = CYOADownloaderGUI._setup_ui
    from .final_behaviors import (
        _sync_legacy_globals as _gui_final_behaviors_sync_legacy_globals,
        _v466_is_cafe_metadata_game_url, _v466_run_download, _v466_setup_ui,
    )
    namespace.update(locals())
    _sync(_gui_final_behaviors_sync_legacy_globals, namespace)
    namespace["run_download"] = _v466_run_download
    _bind_methods(CYOADownloaderGUI, (
        ("_setup_ui", namespace["_APP_FINAL_SETUP_UI"]),
    ), logger=logger, phase="v466")

    from .composition import apply_gui_patches as _phase48_apply_gui_patches
    from .panels import attach_panel_methods as _phase48_attach_panel_methods
    CYOADownloaderGUI = _phase48_attach_panel_methods(_phase48_apply_gui_patches(CYOADownloaderGUI))

    # Final export aliases must match the historical single-file symbol table.
    # Several later namespace.update(locals()) calls intentionally import earlier
    # helper names, so re-pin patch-overridden names just before publishing.
    from .final_behaviors import (
        _v25_manage_offline_viewers as _final_v25_manage_offline_viewers,
        _v25_inject_into_viewer as _final_v25_inject_into_viewer,
    )
    namespace.update({
        "CYOADownloaderGUI": CYOADownloaderGUI,
        "_phase48_apply_gui_patches": _phase48_apply_gui_patches,
        "_phase48_attach_panel_methods": _phase48_attach_panel_methods,
        "_v46_default_progress_expanded": _v462_default_progress_expanded,
        "_v25_manage_offline_viewers": _final_v25_manage_offline_viewers,
        "_v25_inject_into_viewer": _final_v25_inject_into_viewer,
    })

    # Phase 75: after all historical captures/aliases are known, resync every
    # moved GUI patch module once with the final namespace.  In the single-file
    # script these names all shared one global dict; after extraction, functions
    # keep module-local globals, so late-captured names such as
    # _V466_PREVIOUS_SETUP_UI and cross-patch helpers must be mirrored here.
    _resync_modules((
        _gui_app_sync_legacy_globals,
        _gui_final_behaviors_sync_legacy_globals,
    ), namespace, logger, "final")

    _gui_app_sync_legacy_globals(dict(namespace))
    return namespace


def resync_gui_runtime(namespace: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Mirror the final compatibility namespace into moved GUI modules.

    Some compatibility names are imported into ``runtime.surface`` only after
    the historical patch bootstrap runs.  The original single-file script used
    one global dict, so GUI methods could still resolve those later names.
    Calling this after final imports preserves that behavior.
    """
    from .app import _sync_legacy_globals as _app_sync
    from .final_behaviors import _sync_legacy_globals as _final_behaviors_sync

    logger = namespace.get("logger")
    _resync_modules((
        _app_sync, _final_behaviors_sync,
    ), namespace, logger, "runtime resync")
    return namespace


__all__ = ["bootstrap_gui_runtime", "resync_gui_runtime"]



