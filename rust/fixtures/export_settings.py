"""Generate Rust settings parity cases; run from the repository root."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cyoa_downloader_app.config import settings

DOCS = [
    {}, {"_meta": {"schema_version": 4}}, {"archive_strategy": "classic"},
    {"_meta": {"schema_version": 1}, "archive_strategy": "classic"},
    {"dns": "https://dns.example/query"}, {"dns": "1.1.1.1:53"},
    {"proxy": "http://localhost:8080"}, {"archive_max_pages": -5},
    {"archive_max_depth": 999}, {"ai_max_html_chars": 999999999},
    {"dns_port": "65536", "dns_timeout": "0"}, {"ai_enabled": " YES "},
    {"serve_enabled": 2}, {"theme_mode": "dark"}, {"language": "FR"},
    {"ai_temperature": "NaN"}, {"ai_temperature": 9},
    {"cyoa_mgr_enabled": "yes"}, {"cyoa_mgr_db_path": 7},
    {"_section_01_demo": "heading", "discord_enabled": True},
    {"unknown_extension": {"nested": [1, True, None]}},
    {"unicode": "???? ?? cafÃ©"}, {"nested": {"array": [1, 2, {"x": "y"}]}},
    {"settings": {"archive_strategy": "smart", "language": "id"}},
    {"ai_max_calls_per_download": 10**100},
]
INPUTS = [json.dumps(x, ensure_ascii=False) for x in DOCS]
INPUTS += ["[]", "null", "{broken", "1", "\ufeff{" + '"theme_mode":"Light"}']

class MissingKeyring:
    def get_password(self, *_args):
        return None


def main():
    output = ROOT / "rust/fixtures/settings/cases.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    cases = []
    with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"keyring": MissingKeyring()}):
        settings._SETTINGS_FILE = str(Path(td) / "active.json")
        for index, text in enumerate(INPUTS):
            try:
                decoded = json.loads(text.lstrip("\ufeff"))
            except (ValueError, RecursionError):
                decoded = {}
            source = decoded.get("settings") if isinstance(decoded, dict) and isinstance(decoded.get("settings"), dict) else decoded
            if not isinstance(source, dict):
                source = {}
            normalized = settings._normalize_loaded_settings(source, migrate=False)
            formatted = settings._format_settings_json(normalized)
            target = Path(td) / f"case-{index}.json"
            target.write_text(text, encoding="utf-8-sig" if text.startswith("\ufeff") else "utf-8")
            settings._SETTINGS_FILE = str(target)
            loaded = settings._load_settings()
            cases.append({"input": text, "normalized": normalized, "formatted": formatted,
                          "loaded": loaded, "corrupt_created": Path(str(target) + ".corrupt").exists()})
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()


