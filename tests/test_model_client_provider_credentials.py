"""Provider-specific credentials (Groq, Google) and rejection of invalid providers."""

import pytest

from content_pipeline.model_client import LangChainTransport
from content_pipeline.models import Draft


@pytest.fixture
def captured_chat(monkeypatch, tmp_path):
    import langchain.chat_models

    assert not (tmp_path / ".env").exists()
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
    return captured, runnable


@pytest.mark.parametrize("effort", ["none", "medium"])
def test_groq_uses_conventional_credential_and_function_calling(
    tmp_path, monkeypatch, captured_chat, effort
):
    captured, runnable = captured_chat
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")

    result = LangChainTransport("groq:llama-x", effort, tmp_path).prepare(Draft, 1000)

    assert result is runnable
    assert captured["model"] == "llama-x"
    expected = {
        "model_provider": "groq",
        "api_key": "synthetic-groq-key",
        "max_tokens": 1000,
        "max_retries": 0,
    }
    if effort != "none":
        expected["reasoning_effort"] = effort
    assert captured["options"] == expected
    assert captured["schema"] is Draft
    assert captured["structured"] == {"method": "function_calling", "include_raw": True}


@pytest.mark.parametrize("key", [None, "", " \t "])
def test_groq_requires_nonblank_credential(tmp_path, monkeypatch, captured_chat, key):
    captured, _ = captured_chat
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    if key is not None:
        monkeypatch.setenv("GROQ_API_KEY", key)

    with pytest.raises(ValueError, match="^missing credential for selected provider$"):
        LangChainTransport("groq:llama-x", root=tmp_path).prepare(Draft, 1000)

    assert captured == {}


@pytest.mark.parametrize("provider", ["Bad-Name", "../x", "groq\n"])
def test_invalid_provider_is_rejected(tmp_path, monkeypatch, captured_chat, provider):
    captured, _ = captured_chat
    monkeypatch.setenv(provider.upper() + "_API_KEY", "synthetic-invalid-key")

    with pytest.raises(ValueError, match="^unsupported configured model provider$"):
        LangChainTransport(provider + ":llama-x", root=tmp_path).prepare(Draft, 1000)

    assert captured == {}


def test_google_genai_keeps_gemini_credential_and_json_schema(tmp_path, monkeypatch, captured_chat):
    captured, runnable = captured_chat
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-gemini-key")
    monkeypatch.setenv("GOOGLE_GENAI_API_KEY", "synthetic-wrong-key")

    result = LangChainTransport("google_genai:gemini-2.5-flash", root=tmp_path).prepare(Draft, 1000)

    assert result is runnable
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["options"] == {
        "model_provider": "google_genai",
        "api_key": "synthetic-gemini-key",
        "max_output_tokens": 1000,
        "max_retries": 0,
        "thinking_budget": 0,
    }
    assert captured["schema"] is Draft
    assert captured["structured"] == {"method": "json_schema", "include_raw": True}
