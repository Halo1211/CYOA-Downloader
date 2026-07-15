import json
import tempfile
from pathlib import Path

import cyoa_downloader
from cyoa_downloader_app.config import settings as settings_mod
from cyoa_downloader_app.config.secrets import _is_secret_setting_key, _mask_secret
from cyoa_downloader_app.storage import cache as cache_mod
from cyoa_downloader_app.storage import history as history_mod
from cyoa_downloader_app.storage.resume import load_resume_state, save_resume_state, clear_resume_state


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
        monkeypatch.setattr(cache_mod, "_cache_loaded", False)
        cache_mod._cache_put("https://example.test/a.png", b"x" * 80)
        assert cache_mod._cache_get("https://example.test/a.png") == b"x" * 80

        save_resume_state(tmp, ["ok"], ["bad"])
        assert load_resume_state(tmp) == {"completed": ["ok"], "failed": ["bad"]}
        clear_resume_state(tmp)
        assert load_resume_state(tmp) == {"completed": [], "failed": []}


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
    assert "_meta" not in loaded
    assert '\n\n  "archive_strategy"' in text
    assert text.index('"language"') < text.index('"archive_strategy"') < text.index('"http2_enabled"')


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
    assert invalid_enums["archive_strategy"] == "classic"
    assert invalid_enums["theme_mode"] == "System"

    settings_file.write_text(json.dumps({"_meta": {}, "settings": {
        "language": "id", "archive_strategy": "smart",
    }}), encoding="utf-8")
    envelope = settings_mod._load_settings()
    assert envelope["language"] == "id"
    assert envelope["archive_strategy"] == "smart"
