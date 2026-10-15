"""Public client and installed SDK regression proof; all HTTP stays local."""

import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from content_pipeline.ledger import Ledger
from content_pipeline.model_client import LangChainTransport, ModelClient, ModelFailure
from content_pipeline.models import Draft


class FixedPrices:
    def quote(self, *args):
        return 0.001, "offline-fixture"


def sdk_client(
    tmp_path,
    monkeypatch,
    sequence,
    model="openrouter:dots-studio/dots-3-note-preview:free",
    effort="none",
):
    captures, waits = [], []
    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY"):
        monkeypatch.setenv(key, "synthetic-local-only")

    def handler(request):
        payload = json.loads(request.content)
        captures.append(payload)
        event = sequence[len(captures) - 1]
        if event == "timeout":
            raise httpx.ReadTimeout("synthetic transport failure", request=request)
        finish, text, reported = event
        message = {"role": "assistant", "content": None}
        if text is not None:
            args = {
                "slot_id": "a",
                "attempt": 1,
                "tweets": [{"text": text, "source_ids": ["KB-01"]}],
            }
            message["tool_calls"] = [
                {
                    "id": "call_local",
                    "type": "function",
                    "function": {"name": "Draft", "arguments": json.dumps(args)},
                }
            ]
        response = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "created": 1,
            "model": payload["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }
        if reported:
            response["usage"] = {
                "prompt_tokens": 11,
                "completion_tokens": 23,
                "total_tokens": 34,
                "completion_tokens_details": {"reasoning_tokens": 7},
                "cost": 0.002,
                "cost_details": {"upstream_inference_cost": 0.003},
            }
        return httpx.Response(200, json=response)

    original = ChatOpenAI.__init__

    def initialize(chat, *args, **kwargs):
        kwargs["http_async_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        original(chat, *args, **kwargs)

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(ChatOpenAI, "__init__", initialize)
    credentials = tmp_path / "empty-credentials"
    credentials.mkdir()
    client = ModelClient(
        model,
        Ledger(tmp_path / "ledger.json", 100000, 10),
        tmp_path / "usage.jsonl",
        transport=LangChainTransport(model, effort, credentials),
        prices=FixedPrices(),
        sleep=sleep,
    )
    return client, captures, waits


def usage(tmp_path):
    return [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]


async def invoke(client, output_validator=None):
    return await client.call(
        Draft,
        [{"role": "user", "content": "Return a supported draft."}],
        "a",
        "writer",
        1,
        100,
        output_validator=output_validator,
    )


@pytest.mark.parametrize("text", [None, "This error payload must never be accepted."])
async def test_completion_error_retries_before_parsing_and_keeps_reported_usage(
    tmp_path, monkeypatch, text
):
    client, captures, waits = sdk_client(
        tmp_path, monkeypatch, [("error", text, True), ("tool_calls", "Supported text.", True)]
    )
    validated = []
    result = await invoke(client, lambda value: validated.append(value.tweets[0].text))
    assert result.tweets[0].text == "Supported text."
    assert validated == ["Supported text."]
    assert captures[0] == captures[1]
    assert waits == [1]
    records = usage(tmp_path)
    assert [r["call_id"] for r in records] == ["a/writer/1/1", "a/writer/1/2"]
    assert [
        (r["input_tokens"], r["output_tokens"], r["reasoning_tokens"], r["estimated"], r["status"])
        for r in records
    ] == [(11, 23, 7, False, "confirmed")] * 2
    assert [r["cost_usd"] for r in records] == [0.005, 0.005]
    assert client.ledger.totals() == {
        "tokens": 68,
        "cost_usd": 0.01,
        "uncertain_tokens": 0,
        "uncertain_cost_usd": 0,
    }


async def test_completion_error_exhaustion_is_failed_api_after_three_retries(tmp_path, monkeypatch):
    client, captures, waits = sdk_client(
        tmp_path, monkeypatch, [("error", "Parseable but failed.", True)] * 4
    )
    with pytest.raises(ModelFailure, match="failed_api"):
        await invoke(client)
    assert len(captures) == 4
    assert all(value == captures[0] for value in captures)
    assert waits == [1, 2, 4]
    assert len(usage(tmp_path)) == 4
    assert client.ledger.totals()["tokens"] == 136
    assert all(entry["status"] == "confirmed" for entry in client.ledger.entries.values())


async def test_transport_and_completion_errors_share_one_transient_budget(tmp_path, monkeypatch):
    client, captures, waits = sdk_client(
        tmp_path,
        monkeypatch,
        [
            "timeout",
            ("error", None, True),
            "timeout",
            ("error", None, True),
            ("tool_calls", "Must not be reached.", True),
        ],
    )
    with pytest.raises(ModelFailure, match="failed_api"):
        await invoke(client)
    assert len(captures) == 4
    assert all(value == captures[0] for value in captures)
    assert waits == [1, 2, 4]
    records = usage(tmp_path)
    assert [r["status"] for r in records] == ["uncertain", "confirmed", "uncertain", "confirmed"]
    assert [r["call_id"] for r in records] == [f"a/writer/1/{n}" for n in range(1, 5)]
    assert client.ledger.totals()["uncertain_tokens"] == sum(
        entry["tokens"]
        for entry in client.ledger.entries.values()
        if entry["status"] == "uncertain"
    )
    assert client.ledger.totals()["tokens"] == client.ledger.totals()["uncertain_tokens"] + 68


async def test_error_does_not_consume_repair_or_truncation_opportunities(tmp_path, monkeypatch):
    sequence = [
        ("error", None, True),
        ("length", "Truncated.", True),
        ("stop", None, True),
        ("error", None, True),
        ("tool_calls", "Supported after repair.", True),
    ]
    client, captures, waits = sdk_client(tmp_path, monkeypatch, sequence)
    result = await invoke(client)
    assert result.tweets[0].text == "Supported after repair."
    assert waits == [1, 2]
    assert captures[0] == captures[1]
    assert captures[1]["messages"] == captures[2]["messages"]
    assert captures[2].get("max_completion_tokens", captures[2].get("max_tokens")) == 150
    assert len(captures[3]["messages"]) == 2
    assert captures[3] == captures[4]
    assert len(usage(tmp_path)) == 5


async def test_completion_error_without_reported_usage_retains_estimation(tmp_path, monkeypatch):
    client, captures, waits = sdk_client(
        tmp_path, monkeypatch, [("error", None, False), ("tool_calls", "Supported.", True)]
    )
    assert (await invoke(client)).tweets[0].text == "Supported."
    records = usage(tmp_path)
    assert [r["estimated"] for r in records] == [True, False]
    assert [r["status"] for r in records] == ["confirmed", "confirmed"]
    assert client.ledger.totals()["uncertain_tokens"] == 0
    assert waits == [1]


@pytest.mark.parametrize(
    "model,effort,reasoning,reasoning_effort",
    [
        ("openrouter:dots-studio/dots-3-note-preview:free", "none", {"enabled": False}, None),
        ("openrouter:dots-studio/dots-3-note-preview:free", "low", None, "low"),
        ("openrouter:dots-studio/dots-3-note-preview:free", "medium", None, "medium"),
        ("openrouter:dots-studio/dots-3-note-preview:free", "high", None, "high"),
        ("openai:gpt-4.1-mini", "none", None, None),
        ("openai:gpt-4.1-mini", "medium", None, "medium"),
        ("opencode:gpt-5", "none", None, None),
        ("opencode:gpt-5", "medium", None, "medium"),
    ],
)
async def test_installed_sdk_reasoning_wire_and_positive_output(
    tmp_path, monkeypatch, model, effort, reasoning, reasoning_effort
):
    client, captures, waits = sdk_client(
        tmp_path, monkeypatch, [("tool_calls", "Supported.", True)], model, effort
    )
    assert (await invoke(client)).tweets[0].text == "Supported."
    assert len(captures) == 1 and waits == []
    payload = captures[0]
    assert payload.get("reasoning") == reasoning
    assert payload.get("reasoning_effort") == reasoning_effort
    assert payload["tool_choice"]["function"]["name"] == "Draft"
    assert payload["tools"][0]["function"]["parameters"]["properties"]["tweets"]
    assert usage(tmp_path)[0]["status"] == "confirmed"
