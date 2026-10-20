"""The topic chooser sees a catalog and a pre-filtered pool; code stays the only authority."""

import json
from pathlib import Path
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline_end_to_end import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]


class TopicModels(OfflineModels):
    """Offline models whose topic chooser returns scripted assignments and reasons."""

    def __init__(self, assignments=None, reasons=None, fail=False):
        super().__init__()
        self.assignments = assignments or {}
        self.reasons = reasons or {}
        self.fail = fail

    def factory(self, name, slot_id, context):
        client = super().factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            result = await original(messages, schema, max_tokens)
            if schema.__name__ == "TopicChoices":
                result["parsed"]["assignments"] = self.assignments
                result["parsed"]["reasons"] = self.reasons
                if self.fail:
                    result["parsed"]["slot_id"] = "not-the-batch"
                result["raw"].content = json.dumps(result["parsed"])
            return result

        client.transport = transport
        return client

    def topic_request(self):
        """The payload of the one topic-choice call."""
        (payload,) = [call[3] for call in self.calls if call[2] == "TopicChoices"]
        return payload


def slots(*ids, type="tech-thread", **first):
    return [
        ResolvedSlot(slot_id=id, type=type, **(first if index == 0 else {}))
        for index, id in enumerate(ids)
    ]


async def run(tmp_path, models, plan, name="run"):
    return await run_pipeline(
        ROOT,
        plan,
        tmp_path / name,
        model_factory=models.factory,
        search_factory=search_factory,
    )


def resolved(tmp_path, name="run"):
    return json.loads((tmp_path / name / "plan.resolved.json").read_text(encoding="utf-8"))


async def test_request_carries_catalog_and_the_weeks_taken_topics(tmp_path):
    models = TopicModels()
    plan = slots("a", "b", "c", focus="reentrancy") + slots("m", type="marketing-post")
    await run(tmp_path, models, plan)
    request = models.topic_request()
    assert request["taken"] == {"a": "KB-01 Reentrancy"}
    assert set(request["choices"]) == {"b", "c", "m"}
    offered = {id for ids in request["choices"].values() for id in ids}
    assert set(request["catalog"]) == offered
    assert request["catalog"]["KB-02"] == {
        "title": "Integer overflow and underflow",
        "summary": request["catalog"]["KB-02"]["summary"],
        "category": "arithmetic",
        "severity": "high",
    }
    summary = request["catalog"]["KB-02"]["summary"]
    assert summary and "." not in summary[:-1]
    assert request["catalog"]["ANGLE-service"] == {
        "title": "service",
        "summary": "",
        "category": None,
        "severity": None,
    }
    assert "exclude" not in request


async def test_prefilter_drops_the_weeks_taken_topic_and_recent_runs(tmp_path):
    first = TopicModels()
    first_run = await run(tmp_path, first, slots("a", "b"), "first")
    used = {slot.topic_id for slot in first_run.slots.values()}
    models = TopicModels()
    plan = slots("c", "d", "e", focus="reentrancy")
    await run(tmp_path, models, plan, "second")
    choices = models.topic_request()["choices"]
    assert set(choices) == {"d", "e"}
    for ids in choices.values():
        assert not used & set(ids)
        assert "KB-01" not in ids
        assert len(ids) == 20 - len(used | {"KB-01"})


async def test_slot_with_nothing_left_after_filtering_gets_its_whole_pool(tmp_path):
    await run(tmp_path, TopicModels(), slots("a", "b", "c", type="marketing-post"), "first")
    models = TopicModels()
    await run(tmp_path, models, slots("x", type="marketing-post"), "second")
    assert models.topic_request()["choices"]["x"] == [
        "ANGLE-service",
        "ANGLE-credibility",
        "ANGLE-education",
    ]
    assert "history_exhausted:x" in resolved(tmp_path, "second")["warnings"]


async def test_rejected_proposals_are_logged_and_code_picks_instead(tmp_path):
    models = TopicModels({"a": "KB-18", "b": "KB-18", "c": "KB-99", "d": "ANGLE-service"})
    summary = await run(tmp_path, models, slots("a", "b", "c", "d"))
    warnings = resolved(tmp_path)["warnings"]
    assert summary.slots["a"].topic_id == "KB-18"
    assert "topic_proposal_rejected:b:KB-18" in warnings
    assert "topic_proposal_rejected:c:KB-99" in warnings
    assert "topic_proposal_rejected:d:ANGLE-service" in warnings
    assert "topic_proposal_rejected:a:KB-18" not in warnings
    assert summary.slots["b"].topic_id not in {"KB-18", "KB-99"}
    assert summary.slots["c"].topic_id.startswith("KB-")


async def test_reasons_reach_the_resolved_plan_only_for_accepted_picks(tmp_path):
    models = TopicModels(
        {"a": "KB-18", "b": "KB-18", "c": "KB-02"},
        {"a": "  Fresh angle on upgrades.  ", "b": "Duplicate pick.", "c": "x" * 500, "z": "?"},
    )
    await run(tmp_path, models, slots("a", "b", "c"))
    plan = resolved(tmp_path)
    assert plan["topic_reasons"] == {"a": "Fresh angle on upgrades.", "c": "x" * 200}
    assert [slot["slot_id"] for slot in plan["slots"]] == ["a", "b", "c"]


async def test_plan_without_reasons_still_resolves_and_old_files_still_load(tmp_path):
    await run(tmp_path, TopicModels({"a": "KB-18"}), slots("a"))
    plan = resolved(tmp_path)
    assert plan["topic_reasons"] == {}
    from content_pipeline.contracts import ResolvedPlan

    legacy = {key: value for key, value in plan.items() if key != "topic_reasons"}
    assert ResolvedPlan.model_validate(legacy).topic_reasons == {}


async def test_failed_topic_call_falls_back_to_code_with_no_reasons(tmp_path):
    models = TopicModels({"a": "KB-02"}, {"a": "never used"}, fail=True)
    summary = await run(tmp_path, models, slots("a", "b"))
    plan = resolved(tmp_path)
    assert "topic_selection_failed:code_fallback" in plan["warnings"]
    assert plan["topic_reasons"] == {}
    assert summary.shipped == 2
    # The ranked fallback ran; the scripted KB-02 was never read.
    assert summary.slots["a"].topic_id != "KB-02"
    assert not any(warning.startswith("topic_proposal_rejected") for warning in plan["warnings"])
