"""Record the HTTP requests Python `_ai_call` sends and its parsed replies
(F-043 A2 parity). No network: the shared session is replaced by a recorder
that returns canned responses.

Run from the repo root: python rust/fixtures/export_ai_calls.py
Writes rust/fixtures/ai_calls.json.
"""

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cyoa_downloader_app.integrations import ai_calls  # noqa: E402


class Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class Recorder:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.resp


REPLIES = {
    "anthropic": [
        (200, {"content": [{"type": "text", "text": " https://a/p.json "}, {"type": "tool_use", "x": 1},
                           {"type": "text", "text": "!"}]}),
        (200, {"content": []}),
        (200, {"content": [{"type": "text"}]}),
        (500, {"error": "x"}),
    ],
    "openai": [
        (200, {"output_text": "  hello  "}),
        (200, {"output": [{"content": [{"text": "a"}, {"text": ""}, "junk", {"text": "b "}]}, {"content": None}]}),
        (200, {"output_text": "", "output": []}),
        (401, {"error": "bad key"}),
    ],
    "gemini": [
        (200, {"candidates": [{"content": {"parts": [{"text": "x"}, {"text": " y "}]}}, {"content": {}}]}),
        (200, {"candidates": None}),
        (403, "forbidden"),
    ],
    "ollama": [
        (200, {"response": " local answer "}),
        (200, {"response": 5}),
        (200, {"response": "   "}),
        (404, "no model"),
    ],
    "deepseek": [
        (200, {"choices": [{"message": {"content": " ds "}}]}),
        (200, {"choices": []}),
        (200, {"choices": [{"message": {"content": None}}]}),
    ],
    "qwen": [(200, {"choices": [{"message": {"content": "q"}}]})],
    "groq": [(200, {"choices": [{"message": {"content": "g"}}]}), (429, "slow down")],
    "openrouter": [(200, {"choices": [{"message": {"content": "or"}}]})],
    "custom": [(200, {"choices": [{"message": {"content": "c"}}]})],
}
SETTINGS = [
    {},
    {"ollama_url": "http://10.0.0.9:11434/", "ai_custom_base_url": " http://lm.local:1234/v1/ ",
     "ai_temperature": "0.25", "ai_model": "saved-model"},
    {"ai_custom_base_url": "", "ai_temperature": "warm"},
    {"ai_temperature": 1},
]
CALLS = [
    {"prompt": "Find it.", "system": "", "max_tokens": 300, "model": "", "key": "sk-test"},
    {"prompt": "Ünïcode\nprompt", "system": "Be terse.", "max_tokens": 2000, "model": "m/x y", "key": "k&ey=1"},
    {"prompt": "no key", "system": "", "max_tokens": 10, "model": "", "key": ""},
]

out = []
for provider, replies in REPLIES.items():
    for si, st in enumerate(SETTINGS):
        for ci, call in enumerate(CALLS):
            for ri, (status, payload) in enumerate(replies):
                rec = Recorder(Resp(status, payload))
                with mock.patch.object(ai_calls, "_get_shared_session", return_value=rec), \
                        mock.patch.object(ai_calls, "_load_settings", return_value=dict(st)), \
                        mock.patch("cyoa_downloader_app.integrations.ai_core._load_settings",
                                   return_value=dict(st)):
                    result = ai_calls._ai_call(call["key"], call["prompt"], max_tokens=call["max_tokens"],
                                               system=call["system"], label="t", model=call["model"],
                                               provider=provider)
                out.append({"provider": provider, "settings": st, "call": call,
                            "reply": {"status": status, "body": payload},
                            "requests": rec.calls, "result": result})

path = ROOT / "rust" / "fixtures" / "ai_calls.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", path, len(out), "cases")
