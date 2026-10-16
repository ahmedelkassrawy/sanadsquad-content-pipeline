"""Public ModelClient proof for bounded, privacy-safe per-call diagnostics."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from content_pipeline.ledger import Ledger
from content_pipeline.model_client import ModelClient, ModelFailure
from content_pipeline.models import Draft, UsageRecord


class Prices:
    def quote(self, model, inputs, outputs, cached=0, cache_creation=0):
        return (inputs + outputs) / 1_000_000, "fixture"


def raw(finish="stop", status=None, content=None):
    metadata = {"finish_reason": finish}
    if status is not None:
        metadata["http_status"] = status
    return SimpleNamespace(
        content=content or json.dumps({"slot_id": "s", "attempt": 1, "tweets": [{"text": "ok"}]}),
        usage_metadata={"input_tokens": 10, "output_tokens": 20},
        response_metadata=metadata,
    )


def response(finish="stop", parsed=None, error=None, status=None, content=None):
    return {
        "raw": raw(finish, status, content),
        "parsed": parsed
        if parsed is not None
        else {"slot_id": "s", "attempt": 1, "tweets": [{"text": "ok"}]},
        "parsing_error": error,
    }


class SequenceTransport:
    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    async def __call__(self, messages, schema, max_tokens):
        self.calls.append((messages, max_tokens))
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


def setup(tmp_path, events, sleep=None):
    ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
    client = ModelClient(
        "openai:gpt-4.1-mini",
        ledger,
        tmp_path / "usage.jsonl",
        SequenceTransport(events),
        Prices(),
        sleep=sleep or _sleep,
    )
    return client, ledger


async def _sleep(_):
    return None


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def diagnostic(record, failure, status, finish):
    assert record["failure_kind"] == failure
    assert record["http_status"] == status
    assert record["finish_reason"] == finish


@pytest.mark.parametrize(
    "code,kind", [(503, "server"), (429, "rate_limit"), (402, "payment"), (403, "auth")]
)
async def test_http_failure_diagnostics_preserve_retry_and_accounting(tmp_path, code, kind):
    class HttpError(Exception):
        status_code = code

    client, ledger = setup(
        tmp_path,
        [
            HttpError("dummy SECRET_REQUEST_TOKEN https://fake.invalid/raw-response")
            for _ in range(4)
        ],
    )
    with pytest.raises(ModelFailure):
        await client.call(
            Draft, [{"role": "user", "content": "safe prompt"}], "s", "writer", 1, 100
        )
    rows = records(tmp_path / "usage.jsonl")
    record = rows[0]
    diagnostic(record, kind, code, None)
    if code in {429, 402, 403}:
        assert record["status"] == "confirmed"
        assert record["input_tokens"] == record["output_tokens"] == record["cost_usd"] == 0
        assert ledger.entries[record["call_id"]]["status"] == "released"
    else:
        assert record["status"] == "uncertain"
        assert ledger.entries[record["call_id"]]["status"] == "uncertain"
    assert len(rows) == (4 if code in {429, 503} else 1)
    assert all(row["http_status"] == code for row in rows)
    assert "SECRET_REQUEST_TOKEN" not in (tmp_path / "usage.jsonl").read_text()
    assert "fake.invalid" not in (tmp_path / "usage.jsonl").read_text()


@pytest.mark.parametrize(
    "error,kind", [(TimeoutError("private"), "timeout"), (ConnectionError("private"), "connection")]
)
async def test_transport_failure_is_classified_without_exception_text(tmp_path, error, kind):
    client, _ = setup(tmp_path, [error, response()])
    await client.call(Draft, [{"role": "user", "content": "safe prompt"}], "s", "writer", 1, 100)
    rows = records(tmp_path / "usage.jsonl")
    assert len(rows) == 2
    diagnostic(rows[0], kind, None, None)
    diagnostic(rows[1], None, None, "stop")
    assert "private" not in (tmp_path / "usage.jsonl").read_text()


async def test_finish_retry_truncation_and_parse_repair_keep_one_row_per_dispatch(tmp_path):
    client, ledger = setup(
        tmp_path,
        [
            response("error"),
            response("length"),
            response("stop", parsed=None, error=ValueError("secret parse")),
            response(),
        ],
    )
    await client.call(Draft, [{"role": "user", "content": "safe prompt"}], "s", "writer", 1, 100)
    rows = records(tmp_path / "usage.jsonl")
    assert [r["call_id"] for r in rows] == [f"s/writer/1/{i}" for i in range(1, 5)]
    diagnostic(rows[0], "provider_error", None, "error")
    diagnostic(rows[1], "truncated", None, "length")
    diagnostic(rows[2], "malformed_output", None, "stop")
    diagnostic(rows[3], None, None, "stop")
    assert len(ledger.entries) == len(rows) == 4
    assert ledger.totals()["tokens"] == sum(r["input_tokens"] + r["output_tokens"] for r in rows)
    assert rows[1]["output_tokens"] == 20
    assert "secret parse" not in (tmp_path / "usage.jsonl").read_text()


async def test_repeated_6000_to_9000_truncation_is_recorded_once_per_dispatch(tmp_path):
    class Capture(SequenceTransport):
        def __init__(self):
            super().__init__(
                [response("length", content="a" * 6000), response("length", content="b" * 9000)]
            )
            self.max_tokens = []

        async def __call__(self, messages, schema, max_tokens):
            self.max_tokens.append(max_tokens)
            return await super().__call__(messages, schema, max_tokens)

    ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
    transport = Capture()
    client = ModelClient(
        "openai:gpt-4.1-mini", ledger, tmp_path / "usage.jsonl", transport, Prices(), sleep=_sleep
    )
    with pytest.raises(ModelFailure, match="failed_output"):
        await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 6000)
    rows = records(tmp_path / "usage.jsonl")
    assert transport.max_tokens == [6000, 9000]
    assert [r["call_id"] for r in rows] == ["s/writer/1/1", "s/writer/1/2"]
    assert [r["failure_kind"] for r in rows] == ["truncated", "truncated"]
    assert [r["status"] for r in rows] == ["confirmed", "confirmed"]
    assert len(ledger.entries) == 2
    assert ledger.totals()["tokens"] == sum(r["input_tokens"] + r["output_tokens"] for r in rows)


async def test_cancellation_records_uncertain_then_reraises(tmp_path):
    client, ledger = setup(tmp_path, [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "usage.jsonl")
    diagnostic(row, "unknown", None, None)
    assert row["status"] == "uncertain"
    assert ledger.entries[row["call_id"]]["status"] == "uncertain"


async def test_second_malformed_response_has_one_confirmed_row_and_fails_output(tmp_path):
    malformed = response("stop", parsed=None, error=ValueError("SECRET_REQUEST_TOKEN"))
    client, ledger = setup(tmp_path, [malformed, malformed])
    with pytest.raises(ModelFailure, match="failed_output"):
        await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    rows = records(tmp_path / "usage.jsonl")
    assert len(rows) == len(ledger.entries) == 2
    assert [row["failure_kind"] for row in rows] == ["malformed_output", "malformed_output"]
    assert all(row["status"] == "confirmed" for row in rows)
    assert "SECRET_REQUEST_TOKEN" not in (tmp_path / "usage.jsonl").read_text()


async def test_validator_rejection_unknown_finish_and_malformed_envelope(tmp_path):
    rejected, ledger = setup(tmp_path, [response(), response()])
    calls = 0

    def reject_once(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("rejected")

    await rejected.call(
        Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject_once
    )
    rows = records(tmp_path / "usage.jsonl")
    assert calls == len(rows) == len(ledger.entries) == 2
    assert [row["status"] for row in rows] == ["confirmed", "confirmed"]
    diagnostic(rows[0], "output_rejected", None, "stop")
    diagnostic(rows[1], None, None, "stop")

    unknown, _ = setup(
        tmp_path / "unknown", [response("attacker finish secret https://fake.invalid")]
    )
    await unknown.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "unknown" / "usage.jsonl")
    diagnostic(row, None, None, "unknown")
    assert "attacker finish" not in (tmp_path / "unknown" / "usage.jsonl").read_text()

    invalid_scalar, _ = setup(tmp_path / "invalid-scalar", [response("stop", status=True)])
    await invalid_scalar.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "invalid-scalar" / "usage.jsonl")
    diagnostic(row, None, None, "stop")

    malformed, ledger = setup(tmp_path / "envelope", [{"not_raw": True}])
    with pytest.raises(ModelFailure, match="failed_output"):
        await malformed.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "envelope" / "usage.jsonl")
    diagnostic(row, "malformed_envelope", None, None)
    assert ledger.entries[row["call_id"]]["status"] == "uncertain"


@pytest.mark.parametrize("error", [ValueError("rejected"), TypeError("rejected")])
async def test_validator_rejection_repairs_once_then_returns_on_valid_output(tmp_path, error):
    client, ledger = setup(tmp_path, [response(), response()])
    calls = 0

    def reject_once(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise type(error)("SECRET_REQUEST_TOKEN https://fake.invalid/private")

    result = await client.call(
        Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject_once
    )
    assert isinstance(result, Draft)
    rows = records(tmp_path / "usage.jsonl")
    assert calls == len(rows) == len(ledger.entries) == 2
    assert [row["status"] for row in rows] == ["confirmed", "confirmed"]
    assert [row["failure_kind"] for row in rows] == ["output_rejected", None]
    second_messages = client.transport.calls[1][0]
    assert "malformed_structured_response" in second_messages[-1]["content"]
    assert "SECRET_REQUEST_TOKEN" not in json.dumps(second_messages)
    usage_text = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "SECRET_REQUEST_TOKEN" not in usage_text
    assert "fake.invalid" not in usage_text


@pytest.mark.parametrize("error", [ValueError("rejected"), TypeError("rejected")])
async def test_repeated_validator_rejection_records_both_calls_then_fails_output(tmp_path, error):
    client, ledger = setup(tmp_path, [response(), response()])

    def reject(_):
        raise type(error)(str(error))

    with pytest.raises(ModelFailure, match="failed_output"):
        await client.call(
            Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject
        )
    rows = records(tmp_path / "usage.jsonl")
    assert len(rows) == len(ledger.entries) == 2
    assert [row["failure_kind"] for row in rows] == ["output_rejected", "output_rejected"]
    assert all(row["status"] == "confirmed" for row in rows)


async def test_raw_none_with_valid_parsed_output_remains_accepted(tmp_path):
    client, ledger = setup(
        tmp_path,
        [
            {
                "raw": None,
                "parsed": {"slot_id": "s", "attempt": 1, "tweets": [{"text": "ok"}]},
                "parsing_error": None,
            }
        ],
    )
    result = await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "usage.jsonl")
    assert isinstance(result, Draft)
    assert row["status"] == "confirmed"
    assert row["estimated"] is True
    assert len(ledger.entries) == 1
    assert ledger.entries[row["call_id"]]["status"] == "confirmed"


@pytest.mark.parametrize("error", [RuntimeError("unexpected"), asyncio.CancelledError()])
async def test_unexpected_validator_error_propagates_after_confirmed_charge(tmp_path, error):
    client, ledger = setup(tmp_path, [response()])

    def reject(_):
        raise type(error)(str(error))

    with pytest.raises(type(error)):
        await client.call(
            Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject
        )
    [row] = records(tmp_path / "usage.jsonl")
    assert row["status"] == "confirmed"
    assert row["failure_kind"] == "output_rejected"
    assert ledger.entries[row["call_id"]]["status"] == "confirmed"


async def test_non_string_finish_routing_keeps_legacy_string_coercion(tmp_path):
    class LegacyLength:
        def __str__(self):
            return "length"

    truncated = response()
    truncated["raw"].response_metadata["finish_reason"] = LegacyLength()
    client, ledger = setup(tmp_path, [truncated, response()])
    await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    rows = records(tmp_path / "usage.jsonl")
    assert len(rows) == len(ledger.entries) == 2
    assert rows[0]["failure_kind"] == "truncated"
    assert rows[0]["finish_reason"] == "unknown"
    assert rows[1]["failure_kind"] is None


async def test_finish_string_conversion_error_keeps_confirmed_charge(tmp_path):
    class BrokenFinish:
        def __str__(self):
            raise RuntimeError("private conversion failure")

    result = response()
    result["raw"].response_metadata["finish_reason"] = BrokenFinish()
    client, ledger = setup(tmp_path, [result])
    with pytest.raises(RuntimeError, match="private conversion failure"):
        await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "usage.jsonl")
    assert row["status"] == "confirmed"
    assert row["finish_reason"] == "unknown"
    assert ledger.entries[row["call_id"]]["status"] == "confirmed"


@pytest.mark.parametrize("error_type", [ValueError, TypeError])
@pytest.mark.parametrize("reject_count", [1, 2])
async def test_public_caller_validator_compatibility_baseline(tmp_path, error_type, reject_count):
    client, ledger = setup(tmp_path, [response(), response()])
    calls = 0

    def reject(_):
        nonlocal calls
        calls += 1
        if calls <= reject_count:
            raise error_type("rejected")

    if reject_count == 1:
        result = await client.call(
            Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject
        )
        assert isinstance(result, Draft)
    else:
        with pytest.raises(ModelFailure, match="failed_output"):
            await client.call(
                Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject
            )
    rows = records(tmp_path / "usage.jsonl")
    assert calls == len(rows) == len(ledger.entries) == 2
    assert all(row["status"] == "confirmed" for row in rows)
    assert all(ledger.entries[row["call_id"]]["status"] == "confirmed" for row in rows)


async def test_public_caller_raw_none_with_parsed_valid_compatibility_baseline(tmp_path):
    client, ledger = setup(
        tmp_path,
        [
            {
                "raw": None,
                "parsed": {"slot_id": "s", "attempt": 1, "tweets": [{"text": "ok"}]},
                "parsing_error": None,
            }
        ],
    )
    result = await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "usage.jsonl")
    assert isinstance(result, Draft)
    assert row["status"] == "confirmed"
    assert len(ledger.entries) == 1
    assert ledger.entries[row["call_id"]]["status"] == "confirmed"


async def test_public_caller_missing_raw_key_stays_malformed_envelope(tmp_path):
    client, ledger = setup(
        tmp_path,
        [
            {
                "parsed": {"slot_id": "s", "attempt": 1, "tweets": [{"text": "ok"}]},
                "parsing_error": None,
            }
        ],
    )
    with pytest.raises(ModelFailure, match="failed_output"):
        await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    [row] = records(tmp_path / "usage.jsonl")
    assert row["status"] == "uncertain"
    assert ledger.entries[row["call_id"]]["status"] == "uncertain"


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_public_caller_unexpected_validator_error_compatibility_baseline(
    tmp_path, error_type
):
    client, ledger = setup(tmp_path, [response()])

    def reject(_):
        raise error_type("unexpected")

    with pytest.raises(error_type):
        await client.call(
            Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100, reject
        )
    [row] = records(tmp_path / "usage.jsonl")
    assert row["status"] == "confirmed"
    assert ledger.entries[row["call_id"]]["status"] == "confirmed"


async def test_public_caller_non_string_finish_routing_compatibility_baseline(tmp_path):
    class LegacyLength:
        def __str__(self):
            return "length"

    truncated = response()
    truncated["raw"].response_metadata["finish_reason"] = LegacyLength()
    client, ledger = setup(tmp_path, [truncated, response()])
    await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    rows = records(tmp_path / "usage.jsonl")
    assert len(rows) == len(ledger.entries) == 2
    assert all(row["status"] == "confirmed" for row in rows)


def test_usage_record_diagnostics_are_optional_bounded_and_legacy_compatible():
    legacy = UsageRecord.model_validate(
        {
            "call_id": "s/writer/1/1",
            "model": "fixture",
            "input_tokens": 1,
            "output_tokens": 2,
            "cost_source": "fixture",
        }
    )
    assert (legacy.failure_kind, legacy.http_status, legacy.finish_reason) == (None, None, None)
    with pytest.raises(ValidationError):
        UsageRecord.model_validate({**legacy.model_dump(), "http_status": 999})
    with pytest.raises(ValidationError):
        UsageRecord.model_validate({**legacy.model_dump(), "http_status": True})
    with pytest.raises(ValidationError):
        UsageRecord.model_validate({**legacy.model_dump(), "failure_kind": "attacker controlled"})
    with pytest.raises(ValidationError):
        UsageRecord.model_validate({**legacy.model_dump(), "finish_reason": "attacker controlled"})


async def test_prepare_failure_creates_no_usage_or_ledger_charge(tmp_path):
    class PrepareFailure:
        def prepare(self, schema, max_tokens):
            raise RuntimeError("pre-dispatch only")

    ledger = Ledger(tmp_path / "ledger.json", 100_000, 10)
    client = ModelClient(
        "openai:gpt-4.1-mini", ledger, tmp_path / "usage.jsonl", PrepareFailure(), Prices()
    )
    with pytest.raises(ModelFailure, match="failed_api"):
        await client.call(Draft, [{"role": "user", "content": "safe"}], "s", "writer", 1, 100)
    assert ledger.entries == {}
    assert not (tmp_path / "usage.jsonl").exists()
