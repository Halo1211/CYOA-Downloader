"""Export Python `_redact_sensitive_text` outputs (log redaction parity).

Run from the repo root: python rust/fixtures/export_redact.py
Writes rust/fixtures/redact.json.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.logging_setup import _redact_sensitive_text  # noqa: E402

CASES = [
    "plain message, nothing secret",
    "Authorization: Bearer abc.def-ghi_123",
    "header authorization=Basic dXNlcjpwYXNz",
    "GET https://x.test/a.png?key=SECRET123&size=2",
    "https://x.test/?access_token=tok&sig=zzz#frag",
    "proxy http://bob:hunter2@10.0.0.1:8080 failed",
    "socks5h://u:p@h:1080",
    "api_key=ABCDEF ok",
    "my-api-key: 'quoted value' here",
    '{"discord_bot_token": "xyz.abc", "name": "n"}',
    "password = s3cr3t, user = bob",
    "cookie:session=abc; other",
    "x_TOKEN_y=1",
    "tokens are fine",
    "token",
    "credential:'a b'",
    "Résumé token=ünïcode end",
    "url https://x/?token=a&key=b&sig=c",
]

out = [{"in": c, "out": _redact_sensitive_text(c)} for c in CASES]
path = ROOT / "rust" / "fixtures" / "redact.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", path)
