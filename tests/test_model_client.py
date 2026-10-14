import json
import math
from types import SimpleNamespace

import pytest
import httpx
from langchain_core.messages import AIMessage
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from content_pipeline.ledger import Ledger
from content_pipeline.model_client import ModelClient, ModelFailure
from content_pipeline.models import Draft


class FixedPrices:
    def __init__(self):
        self.quotes = []

    def quote(self, model, inputs, outputs, cached=0, cache_creation=0):
        self.quotes.append((inputs, outputs, cached, cache_creation))
        return (inputs + outputs) * 0.000001, "fixture-price"


class Transport:
    def __init__(self, results):
        self.results = list(results)

    async def __call__(self, messages, schema, max_tokens):
        return self.results.pop(0)


def _draft():
    return {"slot_id": "s", "attempt": 1, "tweets": [{"text": "grounded"}]}


def _raw(content="", usage=None, metadata=None, **fields):
    return SimpleNamespace(content=content, usage_metadata=usage, response_metadata=metadata or {}, **fields)


def _response(raw, parsed=None, error=None):
    return {"raw": raw, "parsed": _draft() if parsed is None and error is None else parsed, "parsing_error": error}


async def _call(tmp_path, raw, parsed=None, error=None, results=None):
    prices = FixedPrices()
    ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
    transport = Transport(results or [_response(raw, parsed, error)])
    client = ModelClient("openai:gpt-4.1-mini", ledger, tmp_path / "usage.jsonl", transport, prices=prices)
    await client.call(Draft, [{"role": "user", "content": "prompt"}], "s", "writer", 1, 100)
    records = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    return client, records, prices


async def test_tool_call_payload_is_estimated_when_raw_text_is_empty(tmp_path):
    arguments = json.dumps(_draft(), separators=(",", ":"))
    raw = _raw("", tool_calls=[{"name": "Draft", "args": _draft()}])

    client, [record], prices = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil(len(arguments) / 3.5)
    assert record["estimated"] is True
    assert record["status"] == "confirmed"
    assert client.ledger.totals()["tokens"] == record["input_tokens"] + record["output_tokens"]
    assert prices.quotes[-1][:2] == (record["input_tokens"], record["output_tokens"])


@pytest.mark.parametrize(
    "usage,provider_usage,content,expected_input,expected_output",
    [
        ({"input_tokens": 0}, {}, "a" * 35, 0, 10),
        ({"output_tokens": 0}, {}, "a" * 35, None, 0),
        ({"input_tokens": None, "output_tokens": -2}, {}, "a" * 35, None, 10),
        ({"input_tokens": "12", "output_tokens": "7"}, {}, "a" * 35, None, 10),
        ({}, {"completion_tokens": 0}, "a" * 35, None, 0),
    ],
)
async def test_partial_or_invalid_metadata_estimates_only_missing_counts(
    tmp_path, usage, provider_usage, content, expected_input, expected_output
):
    raw = _raw(content, usage, {"token_usage": provider_usage})
    client, [record], _ = await _call(tmp_path, raw)

    if expected_input is None:
        assert record["input_tokens"] > 0
    else:
        assert record["input_tokens"] == expected_input
    assert record["output_tokens"] == expected_output
    assert record["estimated"] is True
    assert client.ledger.totals()["tokens"] == record["input_tokens"] + record["output_tokens"]


async def test_text_and_identical_tool_arguments_are_not_counted_twice(tmp_path):
    content = json.dumps(_draft(), separators=(",", ":"))
    raw = _raw(content, tool_calls=[{"name": "Draft", "args": _draft()}])

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil(len(content) / 3.5)
    assert record["estimated"] is True


@pytest.mark.parametrize(
    "content,fields,expected_text",
    [
        ([{"type": "text", "text": "visible"}], {}, '[{"text":"visible","type":"text"}]'),
        ("", {"function_call": {"name": "Draft", "arguments": json.dumps(_draft())}}, json.dumps(_draft())),
    ],
)
async def test_structured_content_and_legacy_function_call_payloads(tmp_path, content, fields, expected_text):
    raw = _raw(content, **fields)

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil(len(expected_text) / 3.5)
    assert record["estimated"] is True


async def test_complete_provider_counts_including_zero_stay_authoritative(tmp_path):
    raw = _raw(
        "x" * 10000,
        {"input_tokens": 0, "output_tokens": 7},
        {"token_usage": {"prompt_tokens": 88, "completion_tokens": 99}},
    )

    _, [record], prices = await _call(tmp_path, raw)

    assert (record["input_tokens"], record["output_tokens"], record["estimated"]) == (0, 7, False)
    assert prices.quotes[-1][:2] == (0, 7)


async def test_malformed_repair_calls_are_estimated_and_billed_separately(tmp_path):
    malformed = _raw("{" + "bad output " * 4)
    repaired = _raw("fixed", {"input_tokens": 0, "output_tokens": 0})
    prices = FixedPrices()
    ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
    client = ModelClient(
        "openai:gpt-4.1-mini",
        ledger,
        tmp_path / "usage.jsonl",
        Transport([_response(malformed, parsed=None, error=ValueError("bad")), _response(repaired)]),
        prices=prices,
    )

    await client.call(Draft, [{"role": "user", "content": "prompt"}], "s", "writer", 1, 100)

    records = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert [record["call_id"] for record in records] == ["s/writer/1/1", "s/writer/1/2"]
    assert records[0]["estimated"] is True
    assert records[0]["output_tokens"] == math.ceil(len(malformed.content) / 3.5)
    assert (records[1]["input_tokens"], records[1]["output_tokens"], records[1]["estimated"]) == (0, 0, False)
    assert ledger.totals()["tokens"] == sum(record["input_tokens"] + record["output_tokens"] for record in records)


async def test_installed_openai_adapter_invalid_tool_arguments_are_billed(tmp_path):
    arguments = '{"tweets":[' + 'broken ' * 100

    def handler(request):
        return httpx.Response(200, json={
            "id": "chatcmpl-offline", "object": "chat.completion", "created": 1,
            "model": "gpt-4.1-mini", "choices": [{"index": 0,
                "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "offline", "type": "function", "function": {
                    "name": "Draft", "arguments": arguments}}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        class AdapterTransport:
            def prepare(self, schema, max_tokens):
                return ChatOpenAI(
                    model="gpt-4.1-mini", api_key="synthetic-offline", max_tokens=max_tokens,
                    max_retries=0, http_async_client=http,
                ).with_structured_output(schema, method="function_calling", include_raw=True)

        ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
        usage_path = tmp_path / "usage.jsonl"
        client = ModelClient("openai:gpt-4.1-mini", ledger, usage_path, AdapterTransport(), FixedPrices())
        with pytest.raises(ModelFailure, match="failed_output"):
            await client.call(Draft, [{"role": "user", "content": "write"}], "s", "writer", 1, 1000)

    records = [json.loads(line) for line in usage_path.read_text().splitlines()]
    assert len(records) == 2
    assert [record["output_tokens"] for record in records] == [math.ceil(len(arguments) / 3.5)] * 2
    assert all(record["estimated"] and record["status"] == "confirmed" for record in records)


async def test_native_tool_content_block_and_tool_calls_are_counted_once(tmp_path):
    args = _draft()
    raw = AIMessage(content_blocks=[{"type": "tool_call", "id": "call-1", "name": "Draft", "args": args}])
    assert len(raw.tool_calls) == 1
    content = json.dumps(raw.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil(len(content) / 3.5)


async def test_distinct_tool_calls_with_identical_arguments_are_both_counted(tmp_path):
    arguments = json.dumps(_draft(), separators=(",", ":"))
    raw = _raw("", tool_calls=[
        {"id": "call-1", "function": {"arguments": arguments}},
        {"id": "call-2", "function": {"arguments": arguments}},
    ])

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil((len(arguments) * 2) / 3.5)


async def test_native_additional_kwargs_mirrors_are_counted_once_by_id(tmp_path):
    arguments = json.dumps(_draft(), separators=(",", ":"))
    calls = [
        {"id": call_id, "type": "function", "function": {"name": "Draft", "arguments": arguments}}
        for call_id in ("call-a", "call-b")
    ]
    raw = AIMessage(content="", additional_kwargs={"tool_calls": calls})

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil((len(arguments) * 2) / 3.5)


async def test_idless_duplicate_tool_calls_keep_occurrence_multiplicity(tmp_path):
    arguments = json.dumps(_draft(), separators=(",", ":"))
    raw = _raw("", tool_calls=[
        {"function": {"arguments": arguments}},
        {"function": {"arguments": arguments}},
    ])

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil((len(arguments) * 2) / 3.5)


async def test_idless_content_tool_call_mirror_is_counted_once(tmp_path):
    args = _draft()
    raw = _raw(
        [{"type": "tool_call", "name": "Draft", "args": args}],
        tool_calls=[{"name": "Draft", "args": args}],
    )
    content = json.dumps(raw.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil(len(content) / 3.5)


async def test_same_id_different_arguments_are_distinct_occurrences(tmp_path):
    first = json.dumps({"a": 1}, separators=(",", ":"))
    second = json.dumps({"b": 2}, separators=(",", ":"))
    raw = _raw("", tool_calls=[
        {"id": "reused", "function": {"arguments": first}},
        {"id": "reused", "function": {"arguments": second}},
    ])

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil((len(first) + len(second)) / 3.5)


@pytest.mark.parametrize("missing_usage", [True, False])
async def test_anthropic_tool_use_input_mirrors_are_counted_once(tmp_path, monkeypatch, missing_usage):
    import httpx2

    arguments = _draft()
    blocks = [
        {"type": "tool_use", "id": call_id, "name": "Draft", "input": arguments}
        for call_id in ("call-a", "call-b")
    ]

    def handler(request):
        return httpx2.Response(200, json={
            "id": "msg_offline", "type": "message", "role": "assistant",
            "model": "claude-3-5-sonnet-20241022", "content": blocks,
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {
                "input_tokens": 0 if not missing_usage else 1,
                "output_tokens": 0 if not missing_usage else 1,
            },
        })

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        import langchain_anthropic.chat_models as anthropic_models

        monkeypatch.setattr(anthropic_models, "_get_default_async_httpx_client", lambda **kwargs: http)

        observed = []

        class AdapterTransport:
            def prepare(self, schema, max_tokens):
                runnable = ChatAnthropic(
                    model_name="claude-3-5-sonnet-20241022", api_key="synthetic-offline",
                    max_tokens_to_sample=max_tokens, max_retries=0,
                ).with_structured_output(schema, method="function_calling", include_raw=True)

                class MissingUsage:
                    async def ainvoke(self, messages):
                        result = await runnable.ainvoke(messages)
                        raw = result["raw"]
                        if missing_usage:
                            raw = raw.model_copy(update={"usage_metadata": None})
                        observed.append((raw.content, raw.tool_calls, raw.invalid_tool_calls))
                        return {**result, "raw": raw}

                return MissingUsage()

        ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
        usage_path = tmp_path / "usage.jsonl"
        prices = FixedPrices()
        client = ModelClient(
            "anthropic:claude-3-5-sonnet-20241022", ledger, usage_path,
            AdapterTransport(), prices,
        )
        await client.call(Draft, [{"role": "user", "content": "write"}], "s", "writer", 1, 100)

    record = json.loads(usage_path.read_text())
    assert record["status"] == "confirmed"
    expected_output = math.ceil(len(json.dumps(
        observed[0][0], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )) / 3.5)
    assert record["estimated"] is missing_usage
    assert record["output_tokens"] == (expected_output if missing_usage else 0)
    assert ledger.totals()["tokens"] == record["input_tokens"] + record["output_tokens"]
    assert prices.quotes[-1][:2] == (record["input_tokens"], record["output_tokens"])


async def test_id_union_across_mirrored_views_preserves_distinct_calls(tmp_path):
    arguments = json.dumps(_draft(), separators=(",", ":"))
    normalized = [
        {"id": call_id, "args": _draft()}
        for call_id in ("call-a", "call-b")
    ]
    additional = [
        {"id": call_id, "function": {"arguments": arguments}}
        for call_id in ("call-b", "call-c")
    ]
    raw = _raw("", tool_calls=normalized, additional_kwargs={"tool_calls": additional})

    _, [record], _ = await _call(tmp_path, raw)

    assert record["output_tokens"] == math.ceil((len(arguments) * 3) / 3.5)
