"""The one record id format: build, parse, unusual names and resumed ledgers."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_pipeline import OfflineModels, search_factory

from content_pipeline.ledger import Ledger
from content_pipeline.model_client import ModelClient
from content_pipeline.models import Draft, ResolvedSlot
from content_pipeline.pipeline import run_pipeline, safe_name
from content_pipeline.record_ids import (
    RecordId,
    call_id,
    next_call_n,
    parse_record_id,
    record_id,
)

ROOT = Path(__file__).resolve().parents[1]

TRAIL_NODES = {
    "research_log": {"research", "research_search"},
    "drafts": {"writer", "validator"},
    "review_report": {"reviewer"},
    "visuals": {"visual"},
    "final": {"final"},
    "advisor": {"advisor"},
}


class FixedPrices:
    def quote(self, *args):
        return 0.00001, "offline-fixture"


def draft_payload(slot="a"):
    return {"slot_id": slot, "attempt": 1, "tweets": [{"text": "grounded"}]}


class Transport:
    """Return one scripted parsed response at the transport seam."""

    def __init__(self, parsed):
        self.parsed = parsed

    async def __call__(self, messages, schema, max_tokens):
        return {
            "raw": SimpleNamespace(
                content=json.dumps(self.parsed),
                usage_metadata={"input_tokens": 2, "output_tokens": 3},
                response_metadata={"finish_reason": "stop"},
            ),
            "parsed": self.parsed,
            "parsing_error": None,
        }


class RetryingTransport:
    """Fail the first dispatch with a retryable status, then answer."""

    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = 0

    async def __call__(self, messages, schema, max_tokens):
        self.calls += 1

        if self.calls == 1:
            error = RuntimeError("synthetic provider outage")
            error.status_code = 500

            raise error

        return await Transport(self.parsed)(messages, schema, max_tokens)


async def no_sleep(seconds):
    return None


def client_for(tmp_path, transport, ledger=None):
    return ModelClient(
        "openai:fixture",
        ledger or Ledger(tmp_path / "ledger.json", 100_000, 10),
        tmp_path / "usage.jsonl",
        transport,
        prices=FixedPrices(),
        sleep=no_sleep,
    )


def usage_rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]


@pytest.mark.parametrize(
    "parts, expected",
    [
        (("thread_1", "writer", 1, 1), "thread_1/writer/1/1"),
        (("thread_1", "research_step_5", 2, 3), "thread_1/research_step_5/2/3"),
        (("marketing_3", "validator", 1, 1), "marketing_3/validator/1/1"),
        (("_run", "advisor", 1, 1), "_run/advisor/1/1"),
        (("a", "research_search", 1, 4), "a/research_search/1/4"),
    ],
)
def test_build_and_parse_round_trip(parts, expected):
    assert record_id(*parts) == expected
    assert call_id(*parts) == expected
    assert str(parse_record_id(expected)) == expected
    assert parse_record_id(expected) == RecordId(*parts)


def test_call_number_defaults_to_the_first_call():
    assert record_id("a", "writer", 2) == "a/writer/2/1"
    assert call_id("a", "writer", 2) == "a/writer/2/1"
    assert parse_record_id("a/writer/2/1").call_n == 1


@pytest.mark.parametrize(
    "name", ["a", "A_b-9", "Thread_1-2", "x" * 64, "slot-9_X", "_run", "_batch"]
)
def test_ids_containing_unusual_names_round_trip(name):
    value = record_id(name, "research_search", 3, 2)

    assert parse_record_id(value) == RecordId(name, "research_search", 3, 2)
    assert parse_record_id(value).slot_id == name


@pytest.mark.parametrize("name", ["A_b-9", "Thread_1-2", "x" * 64, "slot-9_X"])
def test_names_the_plan_validator_accepts_keep_their_own_id(name):
    assert safe_name(name) == name
    assert parse_record_id(record_id(name, "writer", 1)).slot_id == name


def test_a_name_that_would_break_the_format_is_refused():
    with pytest.raises(ValueError, match="unsafe slot"):
        safe_name("a/b")

    with pytest.raises(ValueError, match="slash-free"):
        record_id("a/b", "writer", 1)


@pytest.mark.parametrize("parts", [("a/b", "w", 1, 1), ("a", "w/x", 1, 1), ("", "w", 1, 1)])
def test_parts_that_would_corrupt_the_format_are_refused(parts):
    with pytest.raises(ValueError, match="slash-free"):
        record_id(*parts)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a",
        "a/b",
        "a/b/1",
        "a/b/1/2/3",
        "a/b/x/1",
        "a/b/1/x",
        "a//1/1",
        "/b/1/1",
        "a/b/1/",
        "a/b/ 1/1",
        "a/b/1/+1",
        "a/b/1/1 ",
    ],
)
def test_malformed_ids_raise_value_error(value):
    with pytest.raises(ValueError, match="malformed record id"):
        parse_record_id(value)


def test_next_call_number_after_a_resumed_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "a/writer/1/1": {"tokens": 10, "cost_usd": 0.01, "status": "confirmed"},
                "a/writer/1/3": {"tokens": 10, "cost_usd": 0.01, "status": "dispatched"},
                "a/writer/1/2b": {"tokens": 10, "cost_usd": 0.01, "status": "confirmed"},
                "a/writer/2/1": {"tokens": 10, "cost_usd": 0.01, "status": "released"},
                "a/writer/10/1": {"tokens": 10, "cost_usd": 0.01, "status": "confirmed"},
                "a/reviewer/1/1": {"tokens": 10, "cost_usd": 0.01, "status": "confirmed"},
                "aa/writer/1/1": {"tokens": 10, "cost_usd": 0.01, "status": "confirmed"},
            }
        ),
        encoding="utf-8",
    )
    ledger = Ledger(path, 100_000, 10)

    assert next_call_n(ledger.entries, "a", "writer", 1) == 4
    assert next_call_n(ledger.entries, "a", "writer", 2) == 2
    assert next_call_n(ledger.entries, "a", "writer", 10) == 2
    assert next_call_n(ledger.entries, "a", "reviewer", 1) == 2
    assert next_call_n(ledger.entries, "a", "visual", 1) == 1
    assert next_call_n({}, "a", "writer", 1) == 1


async def test_model_call_continues_after_a_resumed_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "a/writer/1/1": {"tokens": 10, "cost_usd": 0.01, "status": "confirmed"},
                "a/writer/1/2": {"tokens": 10, "cost_usd": 0.01, "status": "uncertain"},
                "a/writer/1/4": {"tokens": 999, "cost_usd": 0.01, "status": "released"},
            }
        ),
        encoding="utf-8",
    )
    ledger = Ledger(path, 100_000, 10)
    client = client_for(tmp_path, Transport(draft_payload()), ledger)

    await client.call(Draft, [{"role": "user", "content": "prompt"}], "a", "writer", 1, 100)

    [record] = usage_rows(tmp_path)

    assert record["call_id"] == "a/writer/1/5"
    assert (record["slot_id"], record["node"], record["attempt"]) == ("a", "writer", 1)
    assert ledger.entries["a/writer/1/5"]["status"] == "confirmed"


async def test_a_retried_call_takes_the_next_number(tmp_path):
    transport = RetryingTransport(draft_payload())
    client = client_for(tmp_path, transport)

    await client.call(Draft, [{"role": "user", "content": "prompt"}], "a", "writer", 1, 100)

    assert transport.calls == 2
    assert [row["call_id"] for row in usage_rows(tmp_path)] == ["a/writer/1/1", "a/writer/1/2"]
    assert sorted(client.ledger.entries) == ["a/writer/1/1", "a/writer/1/2"]


async def test_offline_run_ids_all_come_from_the_module(tmp_path):
    models = OfflineModels()
    slots = [ResolvedSlot(slot_id="a", type="tech-thread", focus="reentrancy")]
    summary = await run_pipeline(
        ROOT,
        slots,
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    folder = tmp_path / "run"

    assert summary.shipped == 1

    expected = {
        "research_log": {
            record_id("a", "research_search", 1, 1),
            record_id("a", "research", 1),
        },
        "drafts": {record_id("a", "validator", 1)},
        "review_report": {record_id("a", "reviewer", 1)},
        "visuals": {record_id("a", "visual", 1)},
        "final": {record_id("a", "final", 1)},
        "advisor": {record_id("_run", "advisor", 1)},
    }

    for name, nodes in TRAIL_NODES.items():
        records = json.loads((folder / (name + ".json")).read_text(encoding="utf-8"))
        found = set()

        for record in records:
            parsed = parse_record_id(record["record_id"])

            assert (
                call_id(parsed.slot_id, parsed.node, parsed.attempt, parsed.call_n)
                == (record["record_id"])
            )
            assert (parsed.slot_id, parsed.attempt) == (record["slot_id"], record["attempt"])
            assert parsed.node in nodes

            found.add(record["record_id"])

        assert found == expected[name]

    ledger = json.loads((folder / "ledger.json").read_text(encoding="utf-8"))
    usage = usage_rows(folder)
    plan = json.loads((folder / "plan.resolved.json").read_text(encoding="utf-8"))
    posts = json.loads((folder / "posts.json").read_text(encoding="utf-8"))
    replay = json.loads((folder / "research-replay.json").read_text(encoding="utf-8"))

    assert {
        call_id("a", "research_step_1", 1, 1),
        call_id("a", "writer", 1, 1),
        call_id("a", "reviewer", 1, 1),
        call_id("a", "visual", 1, 1),
    } <= set(ledger)
    assert {row["call_id"] for row in usage} == set(ledger)
    assert set(ledger) == {
        call_id(parsed.slot_id, parsed.node, parsed.attempt, parsed.call_n)
        for parsed in map(parse_record_id, ledger)
    }
    assert plan["record_id"] == record_id("_run", "plan", 1)
    assert summary.record_id == record_id("_run", "summary", 1)
    assert set(replay) == {record_id("a", "research", 1)}
    assert [post["record_id"] for post in posts] == [record_id("a", "export", 1)]
