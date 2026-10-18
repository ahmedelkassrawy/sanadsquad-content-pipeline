"""Every provider call carries a request timeout, so a stalled endpoint cannot freeze a run."""

import langchain.chat_models

from content_pipeline import model_client
from content_pipeline.models import Draft


def test_transport_passes_the_request_timeout_to_every_provider(tmp_path, monkeypatch):
    captured = []
    real_init = langchain.chat_models.init_chat_model

    def recording_init(name, **options):
        captured.append(options)

        return real_init(name, **options)

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", recording_init)
    monkeypatch.setenv("NVIDIA_API_KEY", "synthetic-unused-key")
    monkeypatch.setenv("MISTRALAI_API_KEY", "synthetic-unused-key")

    for model in ["nvidia:z-ai/glm-5.3-flash", "mistralai:ministral-14b-latest"]:
        model_client.LangChainTransport(model, root=tmp_path).prepare(Draft, 100)

    assert [options["timeout"] for options in captured] == [
        model_client.REQUEST_TIMEOUT_SECONDS,
        model_client.REQUEST_TIMEOUT_SECONDS,
    ]


def test_anthropic_uses_native_json_schema_output(tmp_path, monkeypatch):
    methods = []

    class Chat:
        def with_structured_output(self, schema, method, include_raw):
            methods.append(method)

            return self

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", lambda name, **options: Chat())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-unused-key")

    model_client.LangChainTransport("anthropic:claude-opus-5-5", "low", tmp_path).prepare(
        Draft, 100
    )

    assert methods == ["json_schema"]
