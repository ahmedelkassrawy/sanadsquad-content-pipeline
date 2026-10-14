import json
import math
from types import SimpleNamespace

import pytest

from content_pipeline.ledger import Ledger
from content_pipeline.model_client import ModelClient
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
