import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.config import settings as settings_mod
from cyoa_downloader_app.config.secrets import _is_secret_setting_key, _mask_secret
from cyoa_downloader_app.storage import cache as cache_mod
from cyoa_downloader_app.storage import history as history_mod
from cyoa_downloader_app.storage.resume import (
    clear_resume_state,
    load_resume_state,
    resume_job_key,
    save_resume_state,
)
from cyoa_downloader_app.core.progress import DownloadCancelledError
from cyoa_downloader_app.network import fetch as fetch_mod


def test_phase2_facade_names_still_match_modules():
    assert cyoa_downloader._load_settings is settings_mod._load_settings
    assert cyoa_downloader._cache_get is cache_mod._cache_get
    assert cyoa_downloader._check_history is history_mod._check_history
    assert _is_secret_setting_key("ai_api_key_openai") is True
    assert _mask_secret("abcdefghijkl") == "abcd…ijkl"


def test_phase2_settings_export_redacts_secrets(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        settings_file = Path(tmp) / "settings.json"
        export_file = Path(tmp) / "export.json"
        monkeypatch.setattr(settings_mod, "_SETTINGS_FILE", str(settings_file))
        settings_mod._save_settings({**settings_mod._SETTINGS_DEFAULTS, "ai_api_key_openai": "SECRET", "language": "id"})
        ok, msg = settings_mod.export_settings(str(export_file))
        assert ok, msg
        payload = json.loads(export_file.read_text(encoding="utf-8"))
        assert payload["settings"]["language"] == "id"
        assert "ai_api_key_openai" not in payload["settings"]
        assert "ai_api_key_openai" in payload["_meta"]["redacted_keys"]


def test_settings_full_saves_never_share_a_temporary_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "_SETTINGS_FILE", str(settings_file))

    def save(index):
        settings_mod._save_settings({
            **settings_mod._SETTINGS_DEFAULTS,
            "language": "id" if index % 2 else "en",
            "accent_color": f"#{index:06x}",
        })

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(32)))

    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["language"] in {"id", "en"}
    assert not list(tmp_path.glob("settings.json.*.part"))


def test_settings_updates_from_two_processes_preserve_both_keys(tmp_path):
    settings_file = tmp_path / "settings.json"
    start_file = tmp_path / "start"
    settings_file.write_text("{}", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]

    def code_for(key, value):
        return (
            "import time\n"
            "from pathlib import Path\n"
            "from cyoa_downloader_app.config import settings as s\n"
            f"s._SETTINGS_FILE = {str(settings_file)!r}\n"
            "original = s._save_settings\n"
            "def slow_save(payload):\n"
            "    time.sleep(0.25)\n"
            "    original(payload)\n"
            "s._save_settings = slow_save\n"
            f"start = Path({str(start_file)!r})\n"
            "while not start.exists():\n"
            "    time.sleep(0.01)\n"
            f"s._update_setting({key!r}, {value!r})\n"
        )

    processes = [
        subprocess.Popen([sys.executable, "-c", code_for("language", "id")], cwd=root),
        subprocess.Popen([sys.executable, "-c", code_for("proxy", "http://proxy.test")], cwd=root),
    ]
    start_file.write_text("go", encoding="utf-8")
    for process in processes:
        process.wait(timeout=15)
        assert process.returncode == 0

    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["language"] == "id"
    assert payload["proxy"] == "http://proxy.test"


def test_history_updates_from_two_processes_preserve_both_urls(tmp_path):
    history_file = tmp_path / "history.json"
    start_file = tmp_path / "history-start"
    history_file.write_text("{}", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]

    def code_for(url):
        return (
            "import time\n"
            "from pathlib import Path\n"
            "from cyoa_downloader_app.storage import history as h\n"
            f"h._HISTORY_FILE = {str(history_file)!r}\n"
            "original = h._save_history\n"
            "def slow_save(payload):\n"
            "    time.sleep(0.25)\n"
            "    original(payload)\n"
            "h._save_history = slow_save\n"
            f"start = Path({str(start_file)!r})\n"
            "while not start.exists():\n"
            "    time.sleep(0.01)\n"
            f"h._record_history({url!r}, 'file', 'zip', success=False)\n"
        )

    urls = ["https://one.example/game", "https://two.example/game"]
    processes = [
        subprocess.Popen([sys.executable, "-c", code_for(url)], cwd=root)
        for url in urls
    ]
    start_file.write_text("go", encoding="utf-8")
    for process in processes:
        process.wait(timeout=15)
        assert process.returncode == 0

    payload = json.loads(history_file.read_text(encoding="utf-8"))
    assert set(payload) == set(urls)


def test_history_probe_cancellation_does_not_relabel_completed_download(tmp_path, monkeypatch):
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(history_mod, "_HISTORY_FILE", str(history_file))

    def cancelled_probe(*_args, **_kwargs):
        raise DownloadCancelledError("cancelled after download")

    monkeypatch.setattr(fetch_mod, "fetch_response", cancelled_probe)
    history_mod._record_history(
        "https://example.test/completed", "completed", "zip", success=True,
    )

    payload = json.loads(history_file.read_text(encoding="utf-8"))
    assert payload["https://example.test/completed"]["success"] is True


def test_history_lock_failure_is_nonfatal_to_batch_job(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "_HISTORY_FILE", str(tmp_path / "history.json"))

    @contextmanager
    def unavailable_lock(*_args, **_kwargs):
        raise TimeoutError("history is busy")
        yield

    monkeypatch.setattr(history_mod, "interprocess_file_lock", unavailable_lock)
    # Auxiliary history persistence must not raise into the GUI worker's
    # success/failure handling path.
    history_mod._record_history(
        "https://example.test/failed", "failed", "zip", success=False,
    )


def test_phase2_history_cache_resume(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        hist_file = Path(tmp) / "history.json"
        monkeypatch.setattr(history_mod, "_HISTORY_FILE", str(hist_file))
        history_mod._save_history({"https://example.test": {"success": True}})
        assert history_mod._check_history("https://example.test")["success"] is True

        cache_dir = Path(tmp) / "cache"
        monkeypatch.setattr(cache_mod, "_CACHE_DIR", cache_dir)
        monkeypatch.setattr(cache_mod, "_CACHE_IDX", cache_dir / "index.json")
        monkeypatch.setattr(cache_mod, "_cache_index", {})
        monkeypatch.setattr(cache_mod, "_cache_dirty", {})
        monkeypatch.setattr(cache_mod, "_cache_removed", set())
        monkeypatch.setattr(cache_mod, "_cache_replace_generation", 0)
        monkeypatch.setattr(cache_mod, "_cache_flushed_replace_generation", 0)
        monkeypatch.setattr(cache_mod, "_cache_loaded", False)
        monkeypatch.setattr(cache_mod, "_v465_schedule_cache_save", lambda: None)
        cache_mod._cache_put("https://example.test/a.png", b"x" * 80)
        assert cache_mod._cache_get("https://example.test/a.png") == b"x" * 80

        save_resume_state(tmp, ["ok"], ["bad"])
        assert load_resume_state(tmp) == {"completed": ["ok"], "failed": ["bad"]}
        clear_resume_state(tmp)
        assert load_resume_state(tmp) == {"completed": [], "failed": []}


def test_resume_identity_includes_output_name_and_requested_mode():
    url = "https://example.test/game"
    first = resume_job_key(url, "first", "zip")
    assert first == resume_job_key(url, "first", "zip")
    assert first != resume_job_key(url, "second", "zip")
    assert first != resume_job_key(url, "first", "website_folder")
    assert first.startswith("job-v2:")


def test_image_cache_uses_two_gb_default_and_auto_evicts_oldest(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(settings_mod, "_SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache_mod, "_CACHE_IDX", cache_dir / "index.json")
    monkeypatch.setattr(cache_mod, "_cache_index", {})
    monkeypatch.setattr(cache_mod, "_cache_dirty", {})
    monkeypatch.setattr(cache_mod, "_cache_removed", set())
    monkeypatch.setattr(cache_mod, "_cache_replace_generation", 0)
    monkeypatch.setattr(cache_mod, "_cache_flushed_replace_generation", 0)
    monkeypatch.setattr(cache_mod, "_cache_loaded", False)
    monkeypatch.setattr(cache_mod, "_v465_schedule_cache_save", lambda: None)

    assert settings_mod._SETTINGS_DEFAULTS["image_cache_max_mb"] == 2048
    settings_mod._save_settings({**settings_mod._SETTINGS_DEFAULTS, "image_cache_max_mb": 1})
    cache_mod._cache_put("https://example.test/old.png", b"a" * 700_000)
    cache_mod._cache_put("https://example.test/new.png", b"b" * 700_000)

    stats = cache_mod._cache_stats()
    assert stats["limit_mb"] == 1
    assert stats["size_mb"] <= 1
    assert len(cache_mod._cache_index) == 1


def test_cache_index_flush_merges_other_process_additions(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_idx = cache_dir / "index.json"
    external_url = "https://other-process.test/image.png"
    local_url = "https://this-process.test/image.png"
    external_digest = "a" * 64
    local_digest = "b" * 64
    cache_idx.write_text(json.dumps({external_url: external_digest}), encoding="utf-8")

    monkeypatch.setattr(cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache_mod, "_CACHE_IDX", cache_idx)
    monkeypatch.setattr(cache_mod, "_cache_index", {local_url: local_digest})
    monkeypatch.setattr(cache_mod, "_cache_dirty", {local_url: local_digest})
    monkeypatch.setattr(cache_mod, "_cache_removed", set())
    monkeypatch.setattr(cache_mod, "_cache_replace_generation", 0)
    monkeypatch.setattr(cache_mod, "_cache_flushed_replace_generation", 0)
    monkeypatch.setattr(cache_mod, "_cache_loaded", True)

    cache_mod._v465_flush_cache_index()

    assert json.loads(cache_idx.read_text(encoding="utf-8")) == {
        external_url: external_digest,
        local_url: local_digest,
    }


def test_cache_missing_file_persists_index_tombstone(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_idx = cache_dir / "index.json"
    url = "https://example.test/missing.png"
    digest = "c" * 64
    cache_idx.write_text(json.dumps({url: digest}), encoding="utf-8")

    monkeypatch.setattr(cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache_mod, "_CACHE_IDX", cache_idx)
    monkeypatch.setattr(cache_mod, "_cache_index", {url: digest})
    monkeypatch.setattr(cache_mod, "_cache_dirty", {})
    monkeypatch.setattr(cache_mod, "_cache_removed", set())
    monkeypatch.setattr(cache_mod, "_cache_replace_generation", 0)
    monkeypatch.setattr(cache_mod, "_cache_flushed_replace_generation", 0)
    monkeypatch.setattr(cache_mod, "_cache_loaded", True)
    monkeypatch.setattr(cache_mod, "_v465_schedule_cache_save", lambda: None)

    assert cache_mod._cache_get(url) is None
    cache_mod._v465_flush_cache_index()

    assert json.loads(cache_idx.read_text(encoding="utf-8")) == {}


def test_corrupt_cache_index_is_not_reparsed_on_every_lookup(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_idx = cache_dir / "index.json"
    cache_idx.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache_mod, "_CACHE_IDX", cache_idx)
    monkeypatch.setattr(cache_mod, "_cache_index", {})
    monkeypatch.setattr(cache_mod, "_cache_dirty", {})
    monkeypatch.setattr(cache_mod, "_cache_removed", set())
    monkeypatch.setattr(cache_mod, "_cache_loaded", False)

    assert cache_mod._cache_get("https://example.test/a.png") is None
    assert cache_mod._cache_loaded is True
    cache_idx.write_text(json.dumps({"https://example.test/a.png": "d" * 64}), encoding="utf-8")
    assert cache_mod._cache_get("https://example.test/a.png") is None
    assert cache_mod._cache_index == {}


def test_active_settings_are_readable_flat_and_metadata_is_not_runtime_state(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "_SETTINGS_FILE", str(settings_file))

    settings_mod._save_settings({
        **settings_mod._SETTINGS_DEFAULTS,
        "archive_strategy": "browser",
        "archive_max_pages": 800,
    })

    text = settings_file.read_text(encoding="utf-8")
    raw = json.loads(text)
    loaded = settings_mod._load_settings()
    assert raw["_meta"]["archive_modes"]["browser"]
    assert raw["archive_strategy"] == "browser"
    assert raw["archive_max_pages"] == 800
    assert raw["_section_03_javascript_website_archive"] == "JAVASCRIPT WEBSITE ARCHIVE / ARSIP WEBSITE"
    assert raw["_meta"]["quick_help"]["discord_bot_token"].startswith("Saved directly")
    assert "discord_token_storage" not in raw
    assert "_meta" not in loaded
    assert '\n\n  "_section_03_javascript_website_archive"' in text
    assert text.index('\n  "language"') < text.index('\n  "archive_strategy"') < text.index('\n  "http2_enabled"')


def test_visual_section_markers_and_obsolete_discord_switch_are_not_runtime_settings():
    normalized = settings_mod._normalize_loaded_settings({
        "_section_01_interface_output": "visual heading",
        "language": "id",
        "discord_enabled": False,
        "discord_token_storage": "keyring",
    })

    assert normalized["language"] == "id"
    assert "_section_01_interface_output" not in normalized
    assert "discord_enabled" not in normalized
    assert "discord_token_storage" not in normalized


def test_hand_edited_settings_are_normalized_and_export_envelope_can_be_loaded(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "_SETTINGS_FILE", str(settings_file))
    settings_file.write_text(json.dumps({
        "archive_strategy": "BROWSER",
        "archive_interaction_policy": "SAFE",
        "archive_max_scroll_steps": "99999",
        "archive_max_interactions": "-5",
        "archive_max_pages": "99999",
        "archive_max_depth": "not-a-number",
        "deep_scan_enabled": "false",
        "theme_mode": "dark",
        "gallery_dl_mode": "force",
        "ai_key_storage": "env",
        "flaresolverr_session_policy": "manual",
        "flaresolverr_proxy_mode": "none",
    }), encoding="utf-8")

    loaded = settings_mod._load_settings()
    assert loaded["archive_strategy"] == "browser"
    assert loaded["archive_interaction_policy"] == "safe"
    assert loaded["archive_max_scroll_steps"] == 1000
    assert loaded["archive_max_interactions"] == 0
    assert loaded["archive_max_pages"] == 5000
    assert loaded["archive_max_depth"] == 30
    assert loaded["deep_scan_enabled"] is False
    assert loaded["theme_mode"] == "Dark"
    assert loaded["gallery_dl_mode"] == "force"
    assert loaded["ai_key_storage"] == "env"
    assert loaded["flaresolverr_session_policy"] == "manual"
    assert loaded["flaresolverr_proxy_mode"] == "none"

    settings_file.write_text(json.dumps({
        "archive_strategy": "unknown", "theme_mode": "neon",
    }), encoding="utf-8")
    invalid_enums = settings_mod._load_settings()
    assert invalid_enums["archive_strategy"] == "auto"
    assert invalid_enums["theme_mode"] == "System"

    settings_file.write_text(json.dumps({"_meta": {}, "settings": {
        "language": "id", "archive_strategy": "smart",
    }}), encoding="utf-8")
    envelope = settings_mod._load_settings()
    assert envelope["language"] == "id"
    assert envelope["archive_strategy"] == "smart"


def test_schema_1_classic_archive_default_migrates_to_auto():
    normalized = settings_mod._normalize_loaded_settings({
        "_meta": {"schema_version": 1},
        "archive_strategy": "classic",
    })
    assert normalized["archive_strategy"] == "auto"

    explicit_current = settings_mod._normalize_loaded_settings({
        "_meta": {"schema_version": 2},
        "archive_strategy": "classic",
    })
    assert explicit_current["archive_strategy"] == "classic"


def test_legacy_network_settings_migrate_to_explicit_transport_and_mode():
    plain = settings_mod._normalize_loaded_settings({
        "dns": "1.1.1.1", "proxy": "http://127.0.0.1:8080",
    })
    assert plain["dns_protocol"] == "udp"
    assert plain["proxy_mode"] == "manual"

    encrypted = settings_mod._normalize_loaded_settings({
        "dns": "https://cloudflare-dns.com/dns-query",
    })
    assert encrypted["dns_protocol"] == "doh"
    assert encrypted["dns_timeout"] == 5
    assert encrypted["dns_fallback_system"] is True
