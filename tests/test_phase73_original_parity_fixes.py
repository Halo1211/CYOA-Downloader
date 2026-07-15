import inspect
import cyoa_downloader as facade


def test_original_compat_signature_names_are_preserved():
    # These symbols are private-but-exported through the historical facade and
    # were easy to drift during legacy.py deletion / re-export ordering cleanup.
    assert list(inspect.signature(facade._preview_token_valid).parameters) == ["tok"]
    assert list(inspect.signature(facade._v46_default_progress_expanded).parameters) == ["_screen_height"]
    assert list(inspect.signature(facade._v25_manage_offline_viewers).parameters) == ["self"]
    assert list(inspect.signature(facade._v25_inject_into_viewer).parameters) == ["self", "viewer_meta", "parent_win"]
    assert list(inspect.signature(facade._register_builtin_plugins).parameters) == []


def test_original_compat_gui_patch_symbols_are_final_bodies_not_lazy_wrappers():
    assert facade._v25_manage_offline_viewers.__module__ == "cyoa_downloader_app.gui.final_behaviors"
    assert facade._v25_inject_into_viewer.__module__ == "cyoa_downloader_app.gui.final_behaviors"
    assert facade._v46_default_progress_expanded.__module__ == "cyoa_downloader_app.gui.final_behaviors"


def test_original_compat_fetch_response_keeps_v46_base_alias():
    assert facade._v46_fetch_response_legacy.__module__ == "cyoa_downloader_app.network.fetch_base"
    assert facade.fetch_response("file:///not-a-network-url", quiet=True) is None


def test_original_compat_moved_private_globals_remain_resolvable():
    moved_private_names = [
        "_ACTIVE_CANCEL_EVENT",
        "_BEARER_LOG_RE",
        "_CYOA_CAFE_CACHE",
        "_CYOA_CAFE_CACHE_LOCK",
        "_CYOA_CAFE_FIELDS",
        "_MANIFEST_HASH_CHUNK",
        "_MANIFEST_NAME",
        "_PROGRESS_EVENT_SINK",
        "_SECRET_LOG_RE",
        "_VERIFY_JSON_PATH_RE",
        "_VERIFY_LOCAL_REF_RE",
        "_file_handler",
        "_hash_lock",
        "_image_hash_map",
        "_stream_handler",
        "_v465_cache_save_event",
        "_v465_cache_writer_lock",
        "_v465_cache_writer_thread",
    ]
    for name in moved_private_names:
        assert hasattr(facade, name), name


