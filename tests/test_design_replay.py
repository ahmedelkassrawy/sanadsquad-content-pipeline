import asyncio
import json
from unittest.mock import patch

import pytest
from langgraph.errors import NodeCancelledError

from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import Pipeline, run_pipeline
from test_day3_delivery import DeliveryModels, ROOT, records, search_factory


@pytest.mark.parametrize("tamper_receipt", [False, True])
async def test_interrupted_research_reuses_only_identity_bound_saved_pack(tmp_path, tamper_receipt):
    models = DeliveryModels()
    sibling_saved = asyncio.Event()
    a_research_calls = 0
    cancelled = False
    original_record = Pipeline.record
    original_factory = models.factory

    async def record(pipeline, name, entry):
        await original_record(pipeline, name, entry)
        if name == "research_log" and entry.slot_id == "a" and entry.pack is not None:
            sibling_saved.set()

    def factory(name, slot_id, context):
        client = original_factory(name, slot_id, context)
        transport = client.transport

        async def choose_changed_evidence(messages, schema, max_tokens):
            nonlocal a_research_calls, cancelled
            result = await transport(messages, schema, max_tokens)
            if schema.__name__ == "ResearchAction" and slot_id == "a":
                a_research_calls += 1
            if schema.__name__ == "ResearchAction" and slot_id == "a" and a_research_calls > 1:
                parsed = result["parsed"]
                evidence = json.loads(messages[-1]["content"])["evidence"]
                kb02 = next((source for source in evidence if source["source_id"] == "KB-02"), None)
                if parsed["action"] == "finish" and kb02:
                    parsed["source_ids"] = ["KB-02"]
                    parsed["topic_id"] = "KB-02"
                    result["raw"].content = json.dumps(parsed)
            if (
                schema.__name__ == "ResearchAction"
                and slot_id == "b"
                and a_research_calls == 1
                and not cancelled
            ):
                await sibling_saved.wait()
                cancelled = True
                raise asyncio.CancelledError()
            return result

        client.transport = choose_changed_evidence
        return client

    output = tmp_path / "run"
    slots = [
        ResolvedSlot(slot_id="a", type="tech-thread", focus="reentrancy"),
        ResolvedSlot(slot_id="b", type="tech-thread", focus="recursive call"),
    ]
    with patch.object(Pipeline, "record", record), pytest.raises(NodeCancelledError):
        await run_pipeline(
            ROOT, slots, output, model_factory=factory, search_factory=search_factory
        )

    saved = next(
        item for item in records(output, "research_log") if item["slot_id"] == "a" and item["pack"]
    )
    assert saved["pack"]["topic_id"] == "KB-01"
    if tamper_receipt:
        receipt_path = output / "research-replay.json"
        receipts = json.loads(receipt_path.read_text())
        receipts["a/research/1/1"]["request_sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipts))

    summary = await run_pipeline(
        ROOT, resume=output, model_factory=factory, search_factory=search_factory
    )

    if tamper_receipt:
        assert summary.slots["a"].outcome.value == "failed_research"
        assert not any(
            call[1] == "a" and call[2] in {"Draft", "ReviewAssessment"} for call in models.calls
        )
        return

    writer = next(call[3] for call in models.calls if call[1] == "a" and call[2] == "Draft")
    reviewer = next(
        call[3] for call in models.calls if call[1] == "a" and call[2] == "ReviewAssessment"
    )
    persisted = next(
        item for item in records(output, "research_log") if item["slot_id"] == "a" and item["pack"]
    )
    assert writer["research"] == reviewer["research"] == persisted["pack"]
    assert persisted["pack"]["topic_id"] == "KB-01"
    assert summary.shipped == 2
    research_calls = [
        call for call in models.calls if call[2] == "ResearchAction" and call[1] == "a"
    ]
    assert len(research_calls) == 1
    trail = records(output, "research_log")
    assert len({entry["record_id"] for entry in trail}) == len(trail)


async def test_replayed_empty_slot_research_rejects_changed_topic_assignment(tmp_path):
    models = DeliveryModels()
    sibling_saved = asyncio.Event()
    topic_calls = 0
    cancelled = False
    original_record = Pipeline.record
    original_research_slot = Pipeline.research_slot
    original_factory = models.factory

    async def record(pipeline, name, entry):
        await original_record(pipeline, name, entry)
        if name == "research_log" and entry.slot_id == "a" and entry.pack is not None:
            sibling_saved.set()

    def factory(name, slot_id, context):
        client = original_factory(name, slot_id, context)
        transport = client.transport

        async def change_assignment_on_replay(messages, schema, max_tokens):
            nonlocal topic_calls, cancelled
            result = await transport(messages, schema, max_tokens)
            if schema.__name__ == "TopicChoices":
                topic_calls += 1
                result["parsed"]["assignments"] = (
                    {"a": "KB-01", "b": "KB-02"}
                    if topic_calls == 1
                    else {"a": "KB-02", "b": "KB-01"}
                )
                result["raw"].content = json.dumps(result["parsed"])
            return result

        client.transport = change_assignment_on_replay
        return client

    async def interrupt_sibling(pipeline, state):
        nonlocal cancelled
        if state.slot.slot_id == "b" and not cancelled:
            await sibling_saved.wait()
            cancelled = True
            raise asyncio.CancelledError()
        return await original_research_slot(pipeline, state)

    output = tmp_path / "run"
    slots = [ResolvedSlot(slot_id=slot, type="tech-thread") for slot in ("a", "b")]
    with (
        patch.object(Pipeline, "record", record),
        patch.object(Pipeline, "research_slot", interrupt_sibling),
        pytest.raises(NodeCancelledError),
    ):
        await run_pipeline(
            ROOT, slots, output, model_factory=factory, search_factory=search_factory
        )

    summary = await run_pipeline(
        ROOT, resume=output, model_factory=factory, search_factory=search_factory
    )
    assert summary.slots["a"].outcome.value == "failed_research"
    assert summary.slots["b"].outcome.value == "shipped"
    assert not any(call[1] == "a" and call[2] == "Draft" for call in models.calls)
    shipped_topics = [
        slot.topic_id for slot in summary.slots.values() if slot.outcome.value == "shipped"
    ]
    assert shipped_topics == ["KB-01"]


async def test_interrupted_research_keeps_saved_failure_terminal(tmp_path):
    models = DeliveryModels()
    models.behavior = {"a": {"research_mismatch": True}}
    failure_saved = asyncio.Event()
    cancelled = False
    original_record = Pipeline.record
    original_research_slot = Pipeline.research_slot

    async def record(pipeline, name, entry):
        await original_record(pipeline, name, entry)
        if name == "research_log" and entry.record_id == "a/research/1/1" and entry.pack is None:
            failure_saved.set()

    async def interrupt_sibling(pipeline, state):
        nonlocal cancelled
        if state.slot.slot_id == "b" and not cancelled:
            await failure_saved.wait()
            cancelled = True
            raise asyncio.CancelledError()
        return await original_research_slot(pipeline, state)

    output = tmp_path / "run"
    slots = [
        ResolvedSlot(slot_id="a", type="tech-thread", focus="reentrancy"),
        ResolvedSlot(slot_id="b", type="tech-thread", focus="reentrancy"),
    ]
    with (
        patch.object(Pipeline, "record", record),
        patch.object(Pipeline, "research_slot", interrupt_sibling),
        pytest.raises(NodeCancelledError),
    ):
        await run_pipeline(
            ROOT, slots, output, model_factory=models.factory, search_factory=search_factory
        )

    initial_research_calls = [
        call for call in models.calls if call[1] == "a" and call[2] == "ResearchAction"
    ]
    calls_before_resume = len(models.calls)
    external_before = records(output, "external-usage")
    summary = await run_pipeline(
        ROOT, resume=output, model_factory=models.factory, search_factory=search_factory
    )
    assert summary.slots["a"].outcome.value == "failed_research"
    assert not any(
        call[1] == "a" and call[2] in {"ResearchAction", "Draft", "ReviewAssessment"}
        for call in models.calls[calls_before_resume:]
    )
    assert len(
        [call for call in models.calls if call[1] == "a" and call[2] == "ResearchAction"]
    ) == len(initial_research_calls)
    external_after = summary.external_usage.model_dump(mode="json")
    for key in [
        "search_calls",
        "tavily_calls",
        "ddgs_calls",
        "fetch_calls",
        "tavily_credits",
        "tavily_credits_estimated",
    ]:
        assert external_after[key] == external_before[key]
    assert external_after["image_calls"] == external_before["image_calls"] + 1
    assert external_after["pillow_cards"] == external_before["pillow_cards"] + 1
