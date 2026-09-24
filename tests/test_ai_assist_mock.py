import pytest

from cyoa_downloader_app.integrations import ai_calls
from cyoa_downloader_app.integrations.ai_core import (
    AI_MODEL_OPTIONS,
    AI_PROVIDER_DEFAULT_MODEL,
    AIUsageBudget,
    _get_ai_model,
)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.mark.parametrize(
    ("provider", "payload"), [
        ("anthropic", {"content": [{"type": "text", "text": "OK"}]}),
        ("openai", {"output": [{"content": [{"type": "output_text", "text": "OK"}]}]}),
        ("gemini", {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}),
        ("ollama", {"response": "OK"}),
        *[(name, {"choices": [{"message": {"content": "OK"}}]})
          for name in ("deepseek", "qwen", "groq", "openrouter", "custom")],
    ],
)
def test_ai_provider_mock_transport(monkeypatch, provider, payload):
    posted = []

    class Session:
        def post(self, url, **kwargs):
            posted.append((url, kwargs))
            return FakeResponse(payload)

    monkeypatch.setattr(ai_calls, "_get_shared_session", lambda **_kwargs: Session())
    monkeypatch.setattr(ai_calls, "_load_settings", lambda: {
        "ai_custom_base_url": "https://example.com/v1",
        "ollama_url": "http://localhost:11434",
    })
    result = ai_calls._ai_call(
        "" if provider == "ollama" else "mock-key", "Reply OK",
        provider=provider, model=AI_PROVIDER_DEFAULT_MODEL[provider],
    )
    assert result == "OK"
    assert posted
    if provider == "gemini":
        assert AI_PROVIDER_DEFAULT_MODEL[provider] in posted[0][0]
    else:
        assert posted[0][1]["json"]["model"] == AI_PROVIDER_DEFAULT_MODEL[provider]
    assert AI_PROVIDER_DEFAULT_MODEL[provider] in AI_MODEL_OPTIONS[provider]


def test_ai_project_detection_budget_and_url_guard(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_calls, "_ai_call", lambda **kwargs: calls.append(kwargs) or "http://127.0.0.1/private")
    budget = AIUsageBudget(max_calls=1)
    assert ai_calls._ai_detect_project_json(
        "https://example.com", "<html></html>", api_key="mock-key",
        provider="anthropic", budget=budget,
    ) is None
    assert len(calls) == 1
    assert ai_calls._ai_detect_project_json(
        "https://example.com", "<html></html>", api_key="mock-key",
        provider="anthropic", budget=budget,
    ) is None
    assert len(calls) == 1


def test_ai_off_never_calls_provider(monkeypatch):
    monkeypatch.setattr(ai_calls, "_ai_call", lambda **_kwargs: pytest.fail("AI called while off"))
    assert ai_calls._ai_detect_project_json(
        "https://example.com", "<html></html>", api_key="mock-key",
        provider="anthropic", ai_mode="off",
    ) is None


@pytest.mark.parametrize(
    ("provider", "retired"),
    [("deepseek", "deepseek-chat"), ("groq", "llama-3.3-70b-versatile")],
)
def test_saved_retired_model_uses_supported_preset(monkeypatch, provider, retired):
    from cyoa_downloader_app.integrations import ai_core

    monkeypatch.setattr(ai_core, "_load_settings", lambda: {"ai_provider": provider, "ai_model": retired})
    assert _get_ai_model(provider) == AI_PROVIDER_DEFAULT_MODEL[provider]
