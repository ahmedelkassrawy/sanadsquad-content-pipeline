"""Model call outcomes: rate-limit retry, provider refusals, ambiguous failures, retry-after."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from content_pipeline.ledger import Ledger
from content_pipeline.model_client import ModelClient, ModelFailure


class Answer(BaseModel):
    """A minimal structured transport response."""

    text: str


class Prices:
    """Price tokens deterministically without provider metadata."""

    def quote(self, model, inputs, outputs, cached=0, cache_creation=0):
        return (inputs + outputs) / 1000, "fixture"


class ProviderError(Exception):
    """Expose the HTTP diagnostics supplied by provider SDKs."""

    def __init__(self, status, headers=None):
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


class Transport:
    """Return scripted responses or raise errors at the external seam."""

    def __init__(self, *results):
        self.results = iter(results)

    async def __call__(self, messages, schema, max_tokens):
        result = next(self.results)

        if isinstance(result, BaseException):
            raise result

        return result


def success():
    return {
        "raw": SimpleNamespace(
            content="ok",
            usage_metadata={"input_tokens": 2, "output_tokens": 1},
            response_metadata={},
        ),
        "parsed": {"text": "ok"},
    }


def client_for(tmp_path, *results):
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    client = ModelClient(
        "fixture:model",
        Ledger(tmp_path / "ledger.json", 100_000, 100),
        tmp_path / "usage.jsonl",
        transport=Transport(*results),
        prices=Prices(),
        sleep=sleep,
    )
    return client, delays


async def call(client):
    return await client.call(Answer, [{"role": "user", "content": "hello"}], "s", "writer", 1, 10)


def records(client):
    return [json.loads(line) for line in client.usage_path.read_text().splitlines()]


async def test_rate_limit_then_success_releases_reservation(tmp_path):
    client, _ = client_for(tmp_path, ProviderError(429), success())

    assert await call(client) == Answer(text="ok")
    assert client.ledger.entries["s/writer/1/1"]["status"] == "released"
    assert client.ledger.totals() == {
        "tokens": 3,
        "cost_usd": 0.003,
        "uncertain_tokens": 0,
        "uncertain_cost_usd": 0,
    }
    first, second = records(client)
    assert first["status"] == second["status"] == "confirmed"
    assert (first["input_tokens"], first["output_tokens"], first["cost_usd"]) == (0, 0, 0)
    assert (first["failure_kind"], first["http_status"]) == ("rate_limit", 429)


@pytest.mark.parametrize(
    "status,kind,failure",
    [
        (400, "failed_api", "unknown"),
        (401, "failed_api", "auth"),
        (403, "failed_api", "auth"),
        (402, "failed_budget", "payment"),
    ],
)
async def test_definite_provider_refusal_is_confirmed_zero_usage(tmp_path, status, kind, failure):
    client, delays = client_for(tmp_path, ProviderError(status))

    with pytest.raises(ModelFailure, match=kind):
        await call(client)

    assert delays == []
    assert client.ledger.totals()["tokens"] == 0
    assert client.ledger.totals()["uncertain_cost_usd"] == 0
    [row] = records(client)
    assert (row["status"], row["input_tokens"], row["output_tokens"], row["cost_usd"]) == (
        "confirmed",
        0,
        0,
        0,
    )
    assert (row["failure_kind"], row["http_status"]) == (failure, status)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionError(),
        ProviderError(408),
        ProviderError(503),
        RuntimeError(),
        asyncio.CancelledError(),
    ],
)
async def test_ambiguous_failures_keep_uncertain_reservations(tmp_path, error):
    client, _ = client_for(tmp_path, error, error, error, error)
    expected = asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else ModelFailure

    with pytest.raises(expected):
        await call(client)

    rows = records(client)
    assert all(row["status"] == "uncertain" for row in rows)
    assert client.ledger.totals()["uncertain_tokens"] == sum(
        row["input_tokens"] + row["output_tokens"] for row in rows
    )
    assert client.ledger.totals()["uncertain_cost_usd"] > 0


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("header,expected", [("2", 2), ("999", 60)])
async def test_retry_after_seconds_are_honoured_and_capped(tmp_path, status, header, expected):
    client, delays = client_for(tmp_path, ProviderError(status, {"Retry-After": header}), success())

    assert await call(client) == Answer(text="ok")
    assert delays == [expected]
