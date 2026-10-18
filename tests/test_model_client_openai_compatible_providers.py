"""NVIDIA, OpenRouter and OpenCode use their own credential and base URL."""

import pytest

from content_pipeline.model_client import REQUEST_TIMEOUT_SECONDS, LangChainTransport
from content_pipeline.models import Draft


@pytest.fixture
def captured_chat(monkeypatch):
    import langchain.chat_models

    captured = {}
    runnable = object()

    class Chat:
        def with_structured_output(self, schema, **kwargs):
            captured["schema"] = schema
            captured["structured"] = kwargs
            return runnable

    def init_chat_model(model, **kwargs):
        captured["model"] = model
        captured["options"] = kwargs
        return Chat()

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", init_chat_model)
    monkeypatch.setattr("dotenv.dotenv_values", lambda *args, **kwargs: {})
    return captured, runnable


@pytest.mark.parametrize("effort", ["none", "medium"])
@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("nvidia", "https://integrate.api.nvidia.com/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("opencode", "https://opencode.ai/zen/v1"),
    ],
)
def test_openai_compatible_provider_uses_own_key_and_base_url(
    tmp_path, monkeypatch, captured_chat, effort, provider, base_url
):
    captured, runnable = captured_chat
    key = f"synthetic-{provider}-key"
    monkeypatch.setenv(provider.upper() + "_API_KEY", key)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-wrong-key")
    name = "nvidia/nemotron-3-super-120b-a12b"

    result = LangChainTransport(f"{provider}:{name}", effort, tmp_path).prepare(Draft, 1000)

    assert result is runnable
    assert captured["model"] == name
    expected = {
        "model_provider": "openai",
        "max_tokens": 1000,
        "max_retries": 0,
        "api_key": key,
        "timeout": REQUEST_TIMEOUT_SECONDS,
    }
    if effort != "none":
        expected["reasoning_effort"] = effort
    elif provider == "openrouter":
        expected["extra_body"] = {"reasoning": {"enabled": False}}
    expected["base_url"] = base_url
    assert captured["options"] == expected
    assert captured["schema"] is Draft
    assert captured["structured"] == {"method": "function_calling", "include_raw": True}
