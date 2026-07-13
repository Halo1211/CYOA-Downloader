"""
CYOA Downloader â€” v1.0 Release (CYOA Manager panel duplicate-UI fix, GUI input validation,
archive decompression caps, atomic settings/cache writes, URL-scheme guard, batched GUI log flush)
Features:
  â€¢ Parallel image downloads (ThreadPoolExecutor)
  â€¢ All image fields: image, backgroundImage, rowBackgroundImage, objectBackgroundImage
  â€¢ Font detection + download (Google Fonts + direct woff/ttf/otf)
  â€¢ Full website download (viewer HTML/CSS/JS + all assets)
  â€¢ Tkinter GUI (auto-launches when run without arguments)
  â€¢ Serve-only Userscript Lab for bundled IntCyoaEnhancer helper-style QoL/debug helpers
  â€¢ All original CLI flags preserved
"""

import sys
import os
import re
import io
import json
import csv
import logging
import base64
import hashlib
import mimetypes
import tempfile
import threading
import time
import uuid
import zipfile
import shutil
import pathlib
import queue as log_queue_module
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse, unquote, quote


# Compatibility surface for the refactored package.
# Code that historically resolved resources next to cyoa_downloader.py
# should still resolve from the public script directory, not this package directory.
_CYOA_LEGACY_PUBLIC_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "cyoa_downloader.py"))

import hashlib as _hashlib
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Refactor Phase 1: low-risk helpers now live in domain modules; legacy imports
# them back so public names and behavior remain compatible.
from ..app_info import (
    _APP_VERSION, _STABILIZATION_PATCH_ID, _GITHUB_RELEASE_API,
    DEFAULT_WAIT_TIME, DEFAULT_MAX_WORKERS,
)
from ..logging_setup import (
    logger, _formatter, _redact_sensitive_text, _SecretRedactionFilter, setup_file_logging,
)
from ..constants.assets import (
    IMAGE_FIELDS, ICC_PLUS_IMAGE_KEYS, AUDIO_FIELDS, BGMLIST_FIELDS,
    _YOUTUBE_URL_RE, _YOUTUBE_ID_RE, _SOUNDCLOUD_URL_RE,
    FONT_EXTENSIONS, IMAGE_EXTENSIONS, AUDIO_EXTENSIONS, VIDEO_EXTENSIONS,
    SCRIPT_EXTENSIONS, STYLE_EXTENSIONS, TEXT_ASSET_EXTENSIONS,
)
from ..constants.modes import (
    _BATCH_VALID_MODES, _PURE_MODES, _CYOAP_MODES, _WEBSITE_MODES, _FOLDER_MODES,
)
from ..core.paths import (
    _is_windows_reserved_basename, _safe_rel_path, _safe_join,
    _safe_archive_rel_path, _safe_archive_join, _copytree_merge_safe,
)
from ..core.url_utils import (
    is_probable_url, _cyoap_local_path, _same_origin, _candidate_urls_for_cyoap_asset,
    _directory_base_url,
)
from ..core.atomic_io import atomic_write_bytes, atomic_write_text, validate_response_content_length
from ..core.cancellation import (
    _emit_progress_event, _cancel_requested, _raise_if_cancelled, _cancel_aware_sleep,
    set_progress_event_sink, clear_progress_event_sink,
)
from ..core.output import prepare_clean_output_folder, _cleanup_recent_part_files
from ..importers.batch import (
    _derive_mode_flags, _normalize_batch_mode, import_queue_items_from_file,
    _google_sheet_csv_export_url, import_queue_items_from_source, write_failed_url_log,
)
from ..diagnostics.reports import (
    _DEPRECATED_BROKEN_ASSET_REPORT, _remove_deprecated_broken_asset_report,
    append_asset_failures_to_backup_report, write_failed_assets_log,
    write_asset_failure_summary, format_backup_report_text,
)
from ..config.secrets import (
    _SETTINGS_SCHEMA_VERSION, _SETTINGS_SECRET_KEYS, _SETTINGS_SECRET_FRAGMENTS,
    _REDACTED_PLACEHOLDER, _is_secret_setting_key, _keyring_module, _mask_secret,
)
from ..config.settings import (
    _SETTINGS_FILE, _SETTINGS_DEFAULTS, _load_settings, _save_settings,
    _SETTINGS_LOCK, _update_setting, _update_settings,
    _THEME_MODE_CANONICAL, _normalize_theme_mode, _normalize_accent_color,
    _system_prefers_dark, _resolve_theme_is_dark, _detect_ffmpeg_path,
    _ffmpeg_install_guide, export_settings, import_settings,
)
from ..storage.history import (
    _HISTORY_FILE, _load_history, _save_history, _check_history,
)
from ..storage.cache import (
    _CACHE_DIR, _CACHE_IDX, _cache_index, _cache_lock, _cache_loaded,
    _cache_load, _cache_get, _cache_stats, _clear_image_cache,
    _v465_flush_cache_index, _v465_cache_writer, _v465_schedule_cache_save, _cache_put,
)
from ..storage.resume import (
    _RESUME_FILE, load_resume_state, save_resume_state, clear_resume_state,
)
from ..preview_assets import (
    _INT_CYOA_ENHANCER_INFO, _BUNDLED_INTCYOAENHANCER_USERSCRIPT,
    userscript_integration_report,
)
from ..integrations.plugins import (
    _PluginRegistry, _ASSET_SCANNER_PLUGINS, _ENGINE_DETECTOR_PLUGINS,
    register_asset_scanner, register_engine_detector,
    run_asset_scanner_plugins, run_engine_detector_plugins,
    _register_builtin_plugins,
)

# Single source of truth for the stabilization marker. Kept free of the version
# number itself (carried by _APP_VERSION) so banners like
# "CYOA Downloader v{_APP_VERSION} Â· {_STABILIZATION_PATCH_ID}" don't read as a
# doubled version. consolidated from 6 scattered redefinitions.

# Serve-only userscript integration metadata.
# v7.4.9 includes a bundled IntCyoaEnhancer-compatible helper script so
# localhost preview never needs to download the userscript from GreasyFork.
# User-provided local .user.js files can still override the bundled helper.





try:
    import tldextract  # type: ignore
except Exception:
    tldextract = None
try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    def BeautifulSoup(*_args, **_kwargs):  # type: ignore
        raise RuntimeError(
            "Missing dependency: beautifulsoup4 is required for HTML/ICC parsing. "
            "Install it with: pip install beautifulsoup4"
        )

try:
    import json5  # type: ignore
except Exception:
    json5 = None

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Constants
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# â”€â”€ Image fields â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Based on audit of ICC Plus v2.9.1 (Svelte 5) and ICC Old Viewer (Vue)

# Extra ICC Plus/Svelte image keys. Kept separate for docs/tests and merged
# into the JSON-aware scanner so future ICC Plus keys can be added without
# weakening the generic regex scanner with ambiguous names like "url" or "src".

# â”€â”€ Audio fields â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ICC Plus v2.9.1 audio architecture (from app.js audit):
#   â€¢ BGM:  choice/row has bgmId + useAudioURL (bool)
#           if useAudioURL=true  â†’ bgmId is a direct audio URL (mp3/ogg/etc)
#           if useAudioURL=false â†’ bgmId is a YouTube video ID (cannot go offline)
#   â€¢ SFX:  app.soundEffects[].audio â†’ direct audio URL or base64 data URI
#           (nested â€” requires JSON-aware scanning, not simple field regex)
# The regex-based AUDIO_FIELDS below handles simple flat cases;
# the JSON deep-scanner (_deep_scan_project_assets) handles nested/conditional cases.

# Playlist/multi-track audio fields (ICC Plus v2 loadPlaylist support)

# YouTube URL / ID patterns â€” handled via yt-dlp
# YouTube video IDs look like: dQw4w9WgXcQ (11 alphanumeric+_-)

# SoundCloud â€” supported by yt-dlp, stored as full URL in bgmId



# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Logging
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€



# v32 security guard: redact secret-looking values before they reach console,
# GUI log, or rotating file logs. This is intentionally conservative and only
# touches logging text; settings export/import already has its own denylist.




# â”€â”€ File logging â€” written to <output_dir>/cyoa_downloader.log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Initialized lazily when the first download starts so we know output_dir.



# Phase 62: mutable runtime globals now live in runtime.state and are
# imported here for compatibility with historical private imports.
from ..runtime.state import (
    wait_time, _RUN_DOWNLOAD_LOCK, _LAST_PREVIEW_FOLDER,
)


# [] Removed dead duplicate definition of `GUILogHandler` (superseded by the active v46/v465 implementation later in this file).





# Single source of truth for batch-row â†’ run_download flags.

# Two batch dispatch sites (GUI loop and CLI loop) independently derived the
# zip/both/pure_website/website_output/website_zip_output/engine_mode booleans
# from the canonical mode string. They diverged: the GUI set omitted the bare
# ``pure_website`` and bare ``cyoap_vue`` variants (it only matched the
# ``_zip``/``_folder`` suffixed forms). Because ``_normalize_batch_mode`` lets
# bare ``pure_website`` and ``cyoap_vue`` through unchanged, a TXT/CSV row like
# ``url | name | pure_website`` reached the GUI queue and was silently coerced
# into a normal embed/zip download (project scan NOT skipped), while
# ``cyoap_vue`` never triggered the cyoap_vue probe (engine stayed "standard").
# The CLI handled both correctly. This helper encodes the CLI's correct
# semantics once so both dispatch sites stay in parity; adding a future mode in
# one place can no longer silently mis-dispatch in the other.

# Additive only: for every mode both sites already agreed on, the returned
# flags are byte-identical to the previous inline logic. Inputs/outputs and the
# download concept are unchanged.
# ``icc``/``icc_zip``/``icc_folder`` are legacy CLI aliases that
# ``_normalize_batch_mode`` rewrites to ``website*`` before dispatch, so they
# never reach this helper in practice. They are kept here so the helper is a
# defensive superset of both historical dispatch sites (the CLI loop used to
# treat icc* as website modes); this guarantees no behavior change on any path.




































# [Phase 45-47] moved `_set_http2_enabled` out of legacy.py


# [] Removed dead duplicate definition of `prepare_clean_output_folder` (superseded by the active v46/v465 implementation later in this file).














# â”€â”€ v1.0 Release Feature #10: incremental plugin architecture â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Refactor Phase 21: plugin registry implementation moved to
# cyoa_downloader_app.integrations.plugins and is imported above for compatibility.

# Refactor Phase 16: `_scan_cyoap_assets` moved to domain module; imported below for compatibility.


# [Phase 36] moved `try_download_cyoap_vue_site` to cyoa_downloader_app.project.cyoap_vue

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  GUI
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# â”€â”€ Cloudflare / feature toggle globals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from ..runtime.state import (
    use_cloudscraper, _ytdlp_enabled, _HTTP2_ENABLED,
    _DEEP_SCAN_ENABLED, _SELENIUM_ENABLED, _SERVE_ENABLED, _CHEAT_ENABLED, _ITCH_ENABLED,
)


# [Phase 45-47] moved `_set_deep_scan_enabled` out of legacy.py


# [Phase 45-47] moved `_set_selenium_enabled` out of legacy.py


# [Phase 45-47] moved `_set_serve_enabled` out of legacy.py


# [Phase 45-47] moved `_set_cheat_enabled` out of legacy.py


# â”€â”€ Preview session token (serve/cheat lifecycle, v7.6 rewrite) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Every time a preview server starts, it mints a fresh token. Injected pages and
# the cheat route carry that token; requests bearing an older token are treated
# as belonging to a closed/stale preview and are rejected. This is what stops a
# leftover browser tab from a previous run from re-opening a CYOA that the user
# already closed. Guarded by a lock because the GUI thread mints while server
# threads read.
# Refactor Phase 24: preview-token lifecycle moved to gui/preview_server.py.
from ..core.preview_token import (
    _PREVIEW_TOKEN_LOCK, _PREVIEW_SESSION_TOKEN, _new_preview_token,
    _current_preview_token, _clear_preview_token, _preview_token_valid,
)
from ..core.feature_flags import (
    _set_deep_scan_enabled, _set_selenium_enabled,
    _set_serve_enabled, _set_cheat_enabled,
)
from ..integrations.itch import _set_itch_enabled



# [Phase 45-47] moved `_set_itch_enabled` out of legacy.py

# â”€â”€ Cloudflare / bandwidth runtime state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from ..runtime.state import (
    _CLOUDFLARE_MODE, _FLARESOLVERR_URL, _FLARESOLVERR_SESSION_POLICY,
    _FLARESOLVERR_TIMEOUT, _FLARESOLVERR_WAIT_AFTER, _FLARESOLVERR_PROXY_MODE,
    _FLARESOLVERR_SESSIONS, _FLARESOLVERR_LOCK, _time, _threading,
    _bandwidth_limit_kbps, _bw_lock, _bw_last_time, _bw_bytes_this_window, _gui_speed_cb,
)

# [Phase 45-47] moved `_throttle_bandwidth` out of legacy.py

# â”€â”€ Download history / settings / theme / settings import-export â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Refactor Phase 2: config and low-level storage state now live in domain
# modules and are imported above. AI provider/key-storage logic remains below
# for a later integration-focused phase.

# â”€â”€ AI Assist provider + key storage â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Phase 30: low-risk AI core helpers moved to cyoa_downloader_app.integrations.ai_core.
from ..integrations.ai_core import (
    AI_KEYRING_SERVICE, _VALID_AI_KEY_STORAGE, _VALID_AI_MODES,
    _VALID_AI_PROVIDERS, AI_PROVIDER_LABELS, AI_PROVIDER_ENV_VARS,
    AI_OPENAI_COMPAT_BASE, AI_MODEL_OPTIONS, AI_PROVIDER_DEFAULT_MODEL,
    OLLAMA_DEFAULT_URL, _normalize_ai_provider, _ai_provider_label,
    _ai_env_vars, _ai_primary_env_var, _ai_model_options, _default_ai_model,
    _normalize_ai_key_storage, _normalize_ai_mode, _ai_provider_needs_key,
    _ai_is_available, _ai_mode_allows, _get_ai_int_setting, _coerce_int,
    AIUsageBudget, _ai_budget_consume, _clear_ai_plain_keys,
    _sanitize_ai_candidate_url, _host_is_internal, _allow_internal_hosts,
    _set_allow_internal_hosts, _ssrf_block_cross_origin, _get_ai_provider,
    _get_ai_model, _plain_ai_key_setting, _keyring_username,
    _read_ai_key_from_keyring, _write_ai_key_to_keyring,
    _resolve_ai_api_key, _clear_ai_api_key_storage, _ai_key_status_text,
)

# â”€â”€ CYOA Manager Integration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Phase 27: implementation moved to cyoa_downloader_app.integrations.cyoa_manager.
from ..integrations.cyoa_manager import (
    _CYOA_MANAGER_DB_CANDIDATES, _find_cyoa_manager_db,
    _cyoa_manager_viewer_pref, add_to_cyoa_manager,
    _scan_for_cyoa_manager_db, _list_cyoa_manager_projects,
)


# â”€â”€ Offline Viewer Registry â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Phase 31: registry/archive registration helpers moved to integrations.offline_viewers.registry.
from ..integrations.offline_viewers.registry import (
    _VIEWERS_DIR, _VIEWERS_MANIFEST, VIEWER_TYPE_HINTS, _ICC_MARKER_RE,
    _load_viewers_manifest, _save_viewers_manifest, register_offline_viewer,
    _auto_register_bundled_viewers, _extract_iccplus_subviewers,
    unregister_offline_viewer, get_viewer_for_site,
)


# Phase 32: ICC Plus/offline-viewer HTML injection helpers moved to iccplus module.
from ..integrations.offline_viewers.iccplus import (
    _build_html_interceptor, _inject_into_head, _unique_folder, _html_escape,
    _extract_iccplus_app_and_viewer_config, _apply_iccplus_viewer_config_to_html,
)


# Phase 34: offline viewer injector moved to integrations.offline_viewers.injector.
from ..integrations.offline_viewers.injector import _apply_offline_viewer





# Refactor Phase 2: history load/save/check helpers are imported from
# cyoa_downloader_app.storage.history.

from ..runtime.state import _shared_session, _shared_session_cf


# [] Removed dead duplicate definition of `_get_shared_session` (superseded by the active v46/v465 implementation later in this file).


# â”€â”€ Domain rate limiter / backoff state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from ..runtime.state import (
    _domain_last_request, _domain_lock, _domain_min_interval,
    _random, _domain_backoff, _domain_fail_count, _domain_backoff_lock,
    _BACKOFF_BASE, _BACKOFF_MAX, _BACKOFF_JITTER,
)

# [] Removed dead duplicate definition of `_domain_throttle` (superseded by the active v46/v465 implementation later in this file).

# [Phase 45-47] moved `_domain_record_success` out of legacy.py

# [] Removed dead duplicate definition of `_domain_record_failure` (superseded by the active v46/v465 implementation later in this file).

# â”€â”€ E: Persistent disk image cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Refactor Phase 2: image cache state and helpers are imported from
# cyoa_downloader_app.storage.cache.

# Refactor Phase 22: desktop notification and update-check helpers moved to
# cyoa_downloader_app.diagnostics.updates.
from ..diagnostics.updates import (
    _send_desktop_notification, _check_for_app_updates, _batch_check_updates,
)


# Phase 33: AI network/analyzer helpers moved to cyoa_downloader_app.integrations.ai_calls.
from ..integrations.ai_calls import (
    _extract_single_ai_url, _ai_detect_project_json, _ai_call,
    _ai_analyze_js_for_assets, _ai_analyze_viewer_logic,
)










# â”€â”€ B: Browser cookie session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Phase 35: browser cookie/headless fetch helpers moved to network.browser.
from ..network.browser import _make_cookie_session, _fetch_headless


# â”€â”€ A: Headless browser fetch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# â”€â”€ Layer F: gallery-dl fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# gallery-dl is useful for post/gallery pages that require a supported extractor
# (Pixiv artwork pages, DeviantArt pages, booru post pages). It is NOT reliable
# gallery-dl fallback integration.
# Phase 28: implementation moved to cyoa_downloader_app.integrations.gallery_dl.
from ..integrations.gallery_dl import (
    _GALLERY_DL_HOSTS, _GALLERY_DL_CDN_HOSTS, _gdl_available,
    _gallery_dl_mode, _gallery_dl_path, _gallery_dl_config,
    _set_gallery_dl_mode, _gallery_dl_is_available,
    _is_gallery_dl_candidate, _is_gallery_dl_site,
    _fetch_via_gallery_dl, _gdl_collect_files,
)


# [Phase 18] moved image dedup state/helper to cyoa_downloader_app.download.asset_scan
from ..download.asset_scan import _check_image_dedup


# [Phase 45-47] moved `is_cloudflare_challenge` out of legacy.py



# â”€â”€ Cloudflare / FlareSolverr integration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# [Phase 45-47] moved `_normalize_cloudflare_mode` out of legacy.py


# [Phase 45-47] moved `_display_cloudflare_mode` out of legacy.py


# [Phase 45-47] moved `_normalize_flaresolverr_url` out of legacy.py


# [Phase 45-47] moved `_load_cloudflare_settings` out of legacy.py


# [Phase 45-47] moved `_set_cloudflare_config` out of legacy.py


# [Phase 45-47] moved `_flaresolverr_payload_proxy` out of legacy.py


# [Phase 45-47] moved `_flaresolverr_post` out of legacy.py


# [Phase 45-47] moved `_flaresolverr_session_key` out of legacy.py


# [Phase 45-47] moved `_flaresolverr_get_session` out of legacy.py


# [Phase 45-47] moved `flaresolverr_destroy_sessions` out of legacy.py


# [Phase 45-47] moved `flaresolverr_test_connection` out of legacy.py


# [Phase 45-47] moved `_apply_flaresolverr_solution_to_sessions` out of legacy.py


# [Phase 45-47] moved `_response_from_flaresolverr_solution` out of legacy.py


# [Phase 45-47] moved `fetch_via_flaresolverr` out of legacy.py


# â”€â”€ Resume state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Refactor Phase 2: resume helpers are imported from cyoa_downloader_app.storage.resume.

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Application logo assets
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Transparent variants generated from the project logo. The light variant is
# black for white/light UI; the dark variant is white for dark UI. External
# files in assets/ take precedence so packagers can replace the logo without
# editing code; embedded PNGs keep the single-file script usable.
# Refactor Phase 42: GUI logo assets live in cyoa_downloader_app.gui.assets.
from ..gui.assets import (
    _APP_LOGO_LIGHT_B64, _APP_LOGO_DARK_B64,
    _load_logo_images, _load_window_icon_photo,
)

# [Phase 48] moved `launch_gui` to cyoa_downloader_app.gui.app


# [Phase 48] moved `CYOADownloaderGUI` class body to cyoa_downloader_app.gui.app






# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Core orchestration
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# [Phase 13] moved `_finalize_site_folder` to cyoa_downloader_app.download.package


def run_download(
    url: str,
    file_name: str = "",
    zip_output: bool = False,
    both_output: bool = False,
    website_output: bool = False,
    website_zip_output: bool = True,
    pure_website: bool = False,
    download_fonts: bool = False,
    show_font_analysis: bool = True,
    output_dir: str = "",
    max_workers: int = DEFAULT_MAX_WORKERS,
    engine_mode: str = "standard",
    cyoa_mgr_enabled: bool = False,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "auto_fallback",
    analysis_only: bool = False,
    archive_strategy: str = "",
    archive_max_pages: int = 0,
    archive_max_depth: int = -1,
    archive_capture_interactions: bool = False,
) -> None:
    """Compatibility shim for the Phase 40 moved base downloader."""
    from ..download.orchestrator import _base_run_download
    return _base_run_download(
        url=url, file_name=file_name, zip_output=zip_output,
        both_output=both_output, website_output=website_output,
        website_zip_output=website_zip_output, pure_website=pure_website,
        download_fonts=download_fonts, show_font_analysis=show_font_analysis,
        output_dir=output_dir, max_workers=max_workers, engine_mode=engine_mode,
        cyoa_mgr_enabled=cyoa_mgr_enabled, ai_api_key=ai_api_key,
        ai_provider=ai_provider, ai_mode=ai_mode, analysis_only=analysis_only,
        archive_strategy=archive_strategy,
        archive_max_pages=archive_max_pages,
        archive_max_depth=archive_max_depth,
        archive_capture_interactions=archive_capture_interactions,
    )



# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Image processing  (parallel + all fields)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# [Phase 18] moved placeholder SVG helper/data URI to cyoa_downloader_app.download.asset_scan
from ..download.asset_scan import _make_placeholder_svg, _PLACEHOLDER_DATA_URI
# Refactor Phase 20: `_deep_scan_project_assets` moved to domain module; imported below for compatibility.


# Refactor Phase 19: `_write_failed_images_log` moved to domain module; imported below for compatibility.


# Refactor Phase 19: `_write_youtube_skip_log` moved to domain module; imported below for compatibility.


# Refactor Phase 19: `_find_ffmpeg` moved to domain module; imported below for compatibility.



# â”€â”€ yt-dlp GUI progress callback (set by CYOAApp) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from ..runtime.state import _ytdlp_gui_progress_cb

# Refactor Phase 23: yt-dlp progress hook and audio download implementation moved
# to cyoa_downloader_app.download.audio_download. The transitional GUI/CLI globals
# remain here and are read lazily by the domain module.
from ..download.audio_download import _make_ytdlp_hook, _download_youtube_audio


# Refactor Phase 19: `_patch_youtube_refs_in_json` moved to domain module; imported below for compatibility.


# [Phase 18] moved `_safe_response_text` to cyoa_downloader_app.download.asset_scan
from ..download.asset_scan import _safe_response_text




# [Phase 39] moved `process_images` to cyoa_downloader_app.download.image_pipeline



# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Font utilities
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Refactor Phase 19: `_find_font_urls` moved to domain module; imported below for compatibility.


# Refactor Phase 19: `analyse_fonts` moved to domain module; imported below for compatibility.


# Refactor Phase 19: `_download_fonts_into_folder` moved to domain module; imported below for compatibility.


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Website downloader
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# [Phase 45-47] moved `create_retry_session` out of legacy.py


# â”€â”€ Global proxy config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from ..runtime.state import _active_proxy, _proxy_mode

# [Phase 45-47] moved `_get_active_proxy` out of legacy.py

# [Phase 45-47] moved `_set_active_proxy` out of legacy.py


# â”€â”€ Global DNS config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from ..runtime.state import (
    _socket, _active_dns, _orig_getaddrinfo, DNS_PRESETS, BEBASDNS_DOH_VARIANTS,
    _dns_bypass_local, _dns_cache, _DNS_CACHE_TTL_SECONDS,
)

# [Phase 45-47] moved `_build_dns_query_wire` out of legacy.py


# [Phase 45-47] moved `_parse_dns_address_response` out of legacy.py


# [Phase 45-47] moved `_doh_resolve_via` out of legacy.py


# [Phase 45-47] moved `_dns_resolve_via` out of legacy.py


# [Phase 45-47] moved `_patched_getaddrinfo` out of legacy.py


# [Phase 45-47] moved `_set_active_dns` out of legacy.py


# [Phase 45-47] moved `_get_active_dns` out of legacy.py


# [Phase 37] moved `WebsiteDownloader` to cyoa_downloader_app.download.website


# Refactor Phase 25: runtime diagnostic report moved to diagnostics/runtime.py.
from ..diagnostics.runtime import build_diagnostic_report



# Refactor Phase 25: dependency check report moved to diagnostics/dependency_check.py.
from ..diagnostics.dependency_check import dependency_check_report



# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Offline package validator  [, Seksi H Tier A3/A1]
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Opt-in, read-only diagnostic. Inspects a folder produced by a previous
# download and reports integrity problems WITHOUT touching the network or the
# download pipeline. Strictly additive: no existing flag, mode, output format,
# or folder layout changes. Exposed as ``--verify FOLDER``.

# Local asset reference patterns inside project.json / HTML / CSS / JS.
# [Phase 13] moved `_VERIFY_LOCAL_REF_RE` to cyoa_downloader_app.download.package
# Bare relative asset paths inside JSON string values (ICC "images/x.png" style).
# [Phase 13] moved `_VERIFY_JSON_PATH_RE` to cyoa_downloader_app.download.package

# â”€â”€ Manifest sidecar  [, Seksi H Tier A2] â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Optional, opt-in checksum baseline for a downloaded package. Strictly
# additive: written only when the user runs `--verify FOLDER --write-manifest`
# (never during a normal download), so the default output folder layout is
# unchanged. When present, `--verify` upgrades from "missing reference" checks
# to full checksum verification (detects corrupted/truncated files, not just
# absent ones). File name chosen to be self-describing and unlikely to collide.
# [Phase 13] moved `_MANIFEST_NAME` to cyoa_downloader_app.download.package
# [Phase 13] moved `_MANIFEST_HASH_CHUNK` to cyoa_downloader_app.download.package


# [Phase 13] moved `_hash_file_sha256` to cyoa_downloader_app.download.package


# [Phase 13] moved `_walk_package_files` to cyoa_downloader_app.download.package


# [Phase 13] moved `write_package_manifest` to cyoa_downloader_app.download.package


# [Phase 13] moved `_load_package_manifest` to cyoa_downloader_app.download.package


# [Phase 13] moved `verify_output_package` to cyoa_downloader_app.download.package


# Phase 29: implementation moved to diagnostics.self_test.
from ..diagnostics.self_test import run_internal_self_test

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  CLI entry point
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main() -> None:
    """Compatibility shim for the moved CLI implementation."""
    from .cli import main as _cli_main
    return _cli_main()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Original utility functions  (unchanged logic)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# [Phase 45-47] moved `fetch_response_base` out of legacy.py


# [Phase 14] moved core project parser helpers to cyoa_downloader_app.project.parse
from cyoa_downloader_app.project.parse import (
    try_decode_bytes,
    is_zip_bytes,
    looks_like_project_object,
    looks_like_project_payload,
    extract_balanced_brace_block,
    extract_embedded_project_from_js,
    extract_project_from_archive_bytes,
    parse_jsonish_text,
    normalize_project_payload_text,
    extract_project_text_from_payload,
    extract_json_like_block,
    _extract_website_from_archive_zip_name,
    _ARCHIVE_ORG_CYOA_RE,
)


# [Phase 14] moved `is_zip_bytes` to cyoa_downloader_app.project.parse



# [Phase 14] moved `looks_like_project_object` to cyoa_downloader_app.project.parse


# [Phase 14] moved `looks_like_project_payload` to cyoa_downloader_app.project.parse


# [Phase 14] moved `extract_balanced_brace_block` to cyoa_downloader_app.project.parse



# [Phase 14] moved `extract_embedded_project_from_js` to cyoa_downloader_app.project.parse


# [Phase 14] moved `extract_project_from_archive_bytes` to cyoa_downloader_app.project.parse



# [Phase 14] moved `parse_jsonish_text` to cyoa_downloader_app.project.parse


# [Phase 14] moved `normalize_project_payload_text` to cyoa_downloader_app.project.parse


# [Phase 14] moved `extract_project_text_from_payload` to cyoa_downloader_app.project.parse


# [Phase 15] moved low-risk project discovery helpers to cyoa_downloader_app.project.discover
from cyoa_downloader_app.project.discover import (
    find_candidate_urls_in_text,
    _script_priority,
    find_script_sources,
    _scan_html_for_project_hints,
    find_scripts,
    extract_placeholder_url,
    extract_iframe_urls,
    get_first_folder_from_url,
    extract_app_js_path,
    build_default_project_candidates,
    strip_document_from_url,
)


# [Phase 65] moved `_ARCHIVE_ORG_CYOA_RE` to cyoa_downloader_app.project.parse

# [Phase 14] moved `_extract_website_from_archive_zip_name` to cyoa_downloader_app.project.parse




# [Phase 38] moved `try_project_candidate` to cyoa_downloader_app.project.discover



# [Phase 15] moved script discovery / HTML hint helpers to cyoa_downloader_app.project.discover


# [Phase 17] moved `_parallel_head_check` to cyoa_downloader_app.project.discover


# [Phase 38] moved `get_project_source` to cyoa_downloader_app.project.discover


# [Phase 17] moved `url_file_exists` to cyoa_downloader_app.project.discover


# [Phase 17] moved `_normalize_auto_detect_output` to cyoa_downloader_app.project.discover


# [Phase 17] moved `_auto_detect_output_variant` to cyoa_downloader_app.project.discover


# Refactor Phase 16: `_directory_base_url` moved to domain module; imported below for compatibility.


# Refactor Phase 16: `_probe_cyoap_vue_structure` moved to domain module; imported below for compatibility.


# [Phase 17] moved `auto_detect_mode` to cyoa_downloader_app.project.discover

# [Phase 17] moved `auto_detect_modes_batch` to cyoa_downloader_app.project.discover



# [] Removed dead duplicate definition of `get_iframe_url_from_cyoa_cafe` (superseded by the active v46/v465 implementation later in this file).



# [Phase 14] moved `extract_json_like_block` to cyoa_downloader_app.project.parse


# [Phase 17] moved `get_source` to cyoa_downloader_app.project.discover


# [Phase 17] import live discovery helpers early because later compatibility
# patches capture auto_detect_mode/_auto_detect_output_variant by name.
from cyoa_downloader_app.project.discover import (
    get_source, url_file_exists, _parallel_head_check,
    _normalize_auto_detect_output, _auto_detect_output_variant,
    auto_detect_mode, auto_detect_modes_batch,
)

# [Phase 15] moved simple discovery helpers/default candidate builder to cyoa_downloader_app.project.discover


# [Phase 18] moved `_scan_file_for_assets` to cyoa_downloader_app.download.asset_scan
from ..download.asset_scan import _scan_file_for_assets



# [Phase 39] moved `_deep_scan_and_download_assets` to cyoa_downloader_app.download.image_pipeline




# Refactor Phase 20: `get_headers_for_url` moved to domain module; imported below for compatibility.


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# v7.5.8 â€” Item 8: optional itch.io asset downloader
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Design constraints honored:
#   â€¢ Never required for normal CYOA flow. Gated by _ITCH_ENABLED (default OFF).
#   â€¢ Never crashes on an empty key â€” public mode first, auth only if a key is set.
#   â€¢ Prefers OS keyring; warns clearly when falling back to plaintext settings.
#   â€¢ Does not change any existing output/folder/report format. Saves under an
#     "itch_assets/" subfolder of the chosen output dir using the same path-safety
#     helpers (_safe_join) used elsewhere.
# Public mode resolves the standard itch.io page â†’ CDN asset listing that itch
# already exposes without auth; auth mode (API key) is only used when present.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# Refactor Phase 26: itch.io / itch-dl helpers moved to integrations/itch.py.
from ..integrations.itch import (
    _ITCH_ENABLED, _ITCH_KEYRING_SERVICE, _ITCH_KEYRING_USER,
    _set_itch_enabled, _is_itch_url, _resolve_itch_api_key,
    _itch_session, _which, _itch_probe, detect_itch_backend,
    itch_backend_status, build_itch_command, redact_itch_command,
    itch_test_connection, download_itch_assets,
)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Historical GUI patch bootstrap
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_STABILIZATION_AUDIT_ID = "CYOA-v1.0 Release-STAB-v46-AUDIT"
from ..gui.bootstrap import bootstrap_gui_runtime as _bootstrap_gui_runtime
_bootstrap_gui_runtime(globals())

# Built-in plugins are registered after the GUI patch bootstrap, matching the
# historical single-file import order.
_register_builtin_plugins()

# Refactor Phase 3: network layer functions now live in domain modules.
# They are imported after all historical patches so the public names resolve to
# the final v46/v465-compatible implementations while the sensitive mutable
# globals remain owned by this legacy facade during the transition.
from ..network.sessions import (
    create_retry_session, _v465_reset_shared_sessions, _get_shared_session,
)
from ..network.proxy import _get_active_proxy, _set_active_proxy
from ..network.dns import (
    _build_dns_query_wire, _parse_dns_address_response, _doh_resolve_via,
    _dns_resolve_via, _patched_getaddrinfo, _set_active_dns, _get_active_dns,
)
from ..network.throttle import (
    _set_http2_enabled, _throttle_bandwidth, _domain_record_success,
    _domain_throttle, _domain_record_failure,
)
from ..network.cloudflare import (
    is_cloudflare_challenge, _normalize_cloudflare_mode, _display_cloudflare_mode,
    _normalize_flaresolverr_url, _load_cloudflare_settings, _set_cloudflare_config,
    _flaresolverr_payload_proxy, _flaresolverr_post, _flaresolverr_session_key,
    _flaresolverr_get_session, flaresolverr_destroy_sessions,
    flaresolverr_test_connection, _apply_flaresolverr_solution_to_sessions,
    _response_from_flaresolverr_solution, fetch_via_flaresolverr,
)
from ..network.fetch_base import base_fetch_response as _v46_fetch_response_legacy
from ..network.fetch import fetch_response

# Refactor Phase 4: project parsing/discovery/resolver entry points now live
# in project-domain modules. These imports happen after historical v462/v466
# wrappers so auto-detect and CYOA.CAFE behavior remain the final patched form.
from ..project.parse import (
    try_decode_bytes, looks_like_project_object, looks_like_project_payload,
    extract_balanced_brace_block, extract_embedded_project_from_js,
    extract_project_from_archive_bytes, parse_jsonish_text,
    normalize_project_payload_text, extract_project_text_from_payload,
    extract_json_like_block, _extract_website_from_archive_zip_name,
)
from ..project.discover import (
    find_candidate_urls_in_text, try_project_candidate, _script_priority,
    find_script_sources, _scan_html_for_project_hints, get_project_source,
    get_source, url_file_exists, _parallel_head_check,
    _normalize_auto_detect_output, _auto_detect_output_variant, auto_detect_mode,
    auto_detect_modes_batch, find_scripts, extract_placeholder_url,
    extract_iframe_urls, extract_app_js_path, build_default_project_candidates,
)
from ..project.cyoap_vue import (
    _scan_cyoap_assets, try_download_cyoap_vue_site, _probe_cyoap_vue_structure,
)
from ..project.cyoa_cafe import (
    CYOACafeResolutionError, CYOACafeResolver, get_iframe_url_from_cyoa_cafe,
    _CYOA_CAFE_CACHE_TTL, _CYOA_CAFE_CACHE_MAX,
)


if __name__ == "__main__":
    main()

# Refactor Phase 5: external integrations now have domain-module owners. These
# are imported last so historical patches remain the implementation that the
# compatibility facade exposes, while mutable state stays in this legacy module
# during the transition.
from ..integrations.plugins import (
    _PluginRegistry, _ASSET_SCANNER_PLUGINS, _ENGINE_DETECTOR_PLUGINS,
    register_asset_scanner, register_engine_detector,
    run_asset_scanner_plugins, run_engine_detector_plugins,
    _register_builtin_plugins,
)
from ..integrations.ai import (
    AI_KEYRING_SERVICE, _VALID_AI_KEY_STORAGE, _VALID_AI_MODES,
    _VALID_AI_PROVIDERS, AI_PROVIDER_LABELS, AI_PROVIDER_ENV_VARS,
    AI_OPENAI_COMPAT_BASE, AI_MODEL_OPTIONS, AI_PROVIDER_DEFAULT_MODEL,
    OLLAMA_DEFAULT_URL, AIUsageBudget, _normalize_ai_provider,
    _ai_provider_label, _ai_env_vars, _ai_primary_env_var,
    _ai_model_options, _default_ai_model, _normalize_ai_key_storage,
    _normalize_ai_mode, _ai_provider_needs_key, _ai_is_available,
    _ai_mode_allows, _get_ai_int_setting, _ai_budget_consume,
    _clear_ai_plain_keys, _sanitize_ai_candidate_url, _get_ai_provider,
    _get_ai_model, _plain_ai_key_setting, _read_ai_key_from_keyring,
    _write_ai_key_to_keyring, _resolve_ai_api_key,
    _clear_ai_api_key_storage, _ai_key_status_text, _host_is_internal,
    _host_resolves_internal,
    _set_allow_internal_hosts, _ssrf_block_cross_origin,
    _extract_single_ai_url, _ai_detect_project_json, _ai_call,
    _ai_analyze_js_for_assets, _ai_analyze_viewer_logic,
    _v25_ai_settings_panel, _v27_ai_provider_values, _v27_ai_settings_panel,
)
from ..integrations.cyoa_manager import (
    _CYOA_MANAGER_DB_CANDIDATES, _find_cyoa_manager_db,
    _cyoa_manager_viewer_pref, add_to_cyoa_manager,
    _scan_for_cyoa_manager_db, _list_cyoa_manager_projects,
)
from ..integrations.gallery_dl import (
    _GALLERY_DL_HOSTS, _GALLERY_DL_CDN_HOSTS, _gdl_available,
    _gallery_dl_mode, _gallery_dl_path, _gallery_dl_config,
    _set_gallery_dl_mode, _gallery_dl_is_available,
    _is_gallery_dl_candidate, _is_gallery_dl_site,
    _fetch_via_gallery_dl, _gdl_collect_files,
)
from ..integrations.itch import (
    _ITCH_ENABLED, _ITCH_KEYRING_SERVICE, _ITCH_KEYRING_USER,
    _set_itch_enabled, _is_itch_url, _resolve_itch_api_key,
    _itch_session, _itch_probe, detect_itch_backend, itch_backend_status,
    build_itch_command, redact_itch_command, itch_test_connection,
    download_itch_assets,
)
from ..integrations.offline_viewers.registry import (
    _VIEWERS_DIR, _VIEWERS_MANIFEST, VIEWER_TYPE_HINTS,
    _load_viewers_manifest, _save_viewers_manifest,
    register_offline_viewer, _auto_register_bundled_viewers,
    unregister_offline_viewer, get_viewer_for_site,
)
from ..integrations.offline_viewers.archive_store import (
    _extract_iccplus_subviewers,
)
from ..integrations.offline_viewers.iccplus import (
    _extract_iccplus_app_and_viewer_config,
    _apply_iccplus_viewer_config_to_html,
)
from ..integrations.offline_viewers.injector import (
    _apply_offline_viewer,
)
# Keep public compatibility aliases identical to the final historical GUI patch
# bodies; injector keeps lazy wrappers internally but should not overwrite the
# facade symbols used by source-introspection/user scripts.
from ..gui.final_behaviors import (
    _v25_manage_offline_viewers, _v25_inject_into_viewer,
)
from ..gui.final_behaviors import (
    _v462_default_progress_expanded as _v46_default_progress_expanded,
)

# Refactor Phase 6: download pipeline entry points now have domain-module
# owners. These are imported after all historical run_download/GUI/network
# wrappers so the exported names remain the final patched implementations.
from ..download.orchestrator import (
    run_download, _v462_resolve_pure_download_url, _v462_run_download,
    _v466_run_download, _RUN_DOWNLOAD_LOCK, _LAST_PREVIEW_FOLDER,
)
from ..download.image_pipeline import (
    _make_placeholder_svg, _deep_scan_project_assets, _write_failed_images_log,
    _write_youtube_skip_log, _find_ffmpeg, _make_ytdlp_hook,
    _download_youtube_audio, _patch_youtube_refs_in_json,
    _safe_response_text, process_images, _scan_file_for_assets,
    _deep_scan_and_download_assets, _is_probable_raw_cdn_asset,
    _check_image_dedup,
)
from ..download.fonts import (
    _find_font_urls, analyse_fonts, _download_fonts_into_folder,
)
from ..download.website import (
    WebsiteDownloader, get_headers_for_url, is_zip_bytes, get_source,
    url_file_exists, _directory_base_url, get_first_folder_from_url,
    get_first_subdomain, strip_document_from_url,
)
from ..download.package import (
    _finalize_site_folder, _hash_file_sha256, _walk_package_files,
    write_package_manifest, _load_package_manifest, verify_output_package,
    validate_zip_archive, atomic_stream_response_to_file,
    validate_response_content_length, save_string_to_file, zip_temp_folder,
    prepare_clean_output_folder, _build_output_name, clean_url_path_component,
    create_random_temp_folder, delete_temp_folder, canonicalize_url,
)

# Phase 75: refresh all moved GUI method/patch globals after final compatibility
# imports replace bridge names with the final domain-module implementations.
try:
    from ..gui.bootstrap import resync_gui_runtime as _resync_gui_runtime
    _resync_gui_runtime(globals())
except Exception as _ignored_exc:
    logger.debug("Ignored recoverable exception refreshing GUI globals: %s", _ignored_exc)

_MOVED_PRIVATE_GLOBAL_MODULES = {
    "_SECRET_LOG_RE": "cyoa_downloader_app.logging_setup",
    "_BEARER_LOG_RE": "cyoa_downloader_app.logging_setup",
    "_stream_handler": "cyoa_downloader_app.logging_setup",
    "_file_handler": "cyoa_downloader_app.logging_setup",
    "_PROGRESS_EVENT_SINK": "cyoa_downloader_app.core.cancellation",
    "_ACTIVE_CANCEL_EVENT": "cyoa_downloader_app.core.cancellation",
    "_image_hash_map": "cyoa_downloader_app.download.asset_scan",
    "_hash_lock": "cyoa_downloader_app.download.asset_scan",
    "_VERIFY_LOCAL_REF_RE": "cyoa_downloader_app.download.package",
    "_VERIFY_JSON_PATH_RE": "cyoa_downloader_app.download.package",
    "_MANIFEST_NAME": "cyoa_downloader_app.download.package",
    "_MANIFEST_HASH_CHUNK": "cyoa_downloader_app.download.package",
    "_CYOA_CAFE_FIELDS": "cyoa_downloader_app.project.cyoa_cafe",
    "_CYOA_CAFE_CACHE": "cyoa_downloader_app.project.cyoa_cafe",
    "_CYOA_CAFE_CACHE_LOCK": "cyoa_downloader_app.project.cyoa_cafe",
    "_v465_cache_save_event": "cyoa_downloader_app.storage.cache",
    "_v465_cache_writer_lock": "cyoa_downloader_app.storage.cache",
    "_v465_cache_writer_thread": "cyoa_downloader_app.storage.cache",
}


def __getattr__(name):
    module_name = _MOVED_PRIVATE_GLOBAL_MODULES.get(name)
    if module_name is not None:
        module = __import__(module_name, fromlist=[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


