"""The topic chooser sees a catalog and a pre-filtered pool; code stays the only authority."""

import json
from pathlib import Path
from content_pipeline.contracts import TopicChoiceRequest
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from content_pipeline.post_types import PostTypes
from content_pipeline.research import canonical_topic_choices, topic_wire_schema
from skills.search_kb import KnowledgeBase
from test_pipeline_end_to_end import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]
KB = KnowledgeBase(ROOT / "inputs/kb.json")
TYPES = PostTypes(ROOT)
FOCUSED_ENTRY = KB.entries[0]
ACCEPTED_ENTRY, SECOND_ACCEPTED_ENTRY = TYPES.topic_pool("tech-thread", KB, ranked=True)[:2]
UNKNOWN_ENTRY = "SS-00000"


class TopicModels(OfflineModels):
    """Offline models whose topic chooser returns scripted assignments and reasons."""

    def __init__(self, assignments=None, reasons=None, fail=False):
        super().__init__()
        self.assignments = assignments or {}
        self.reasons = reasons or {}
        self.fail = fail
        self.schemas = []

    def factory(self, name, slot_id, context):
        client = super().factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            result = await original(messages, schema, max_tokens)
            if schema.__name__ == "TopicChoices":
                self.schemas.append(schema.model_json_schema())
                result["parsed"]["assignments"] = self.assignments
                if self.reasons:
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
    plan = slots("a", "b", "c", focus=FOCUSED_ENTRY["title"]) + slots("m", type="marketing-post")
    await run(tmp_path, models, plan)
    request = models.topic_request()
    assert request["taken"] == {"a": f"{FOCUSED_ENTRY['id']} {FOCUSED_ENTRY['title']}"}
    assert set(request["choices"]) == {"b", "c", "m"}
    offered = {id for ids in request["choices"].values() for id in ids}
    assert set(request["catalog"]) == offered
    entry = KB.entries[1]
    assert request["catalog"][entry["id"]] == {
        "title": entry["title"],
        "summary": request["catalog"][entry["id"]]["summary"],
        "category": ", ".join(entry["category"]),
        "severity": entry["impact"],
    }
    summary = request["catalog"][entry["id"]]["summary"]
    assert summary and "." not in summary[:-1]
    angle = TYPES.angles("marketing-post")[0]
    assert request["catalog"][angle.id] == {
        "title": angle.name,
        "summary": "",
        "category": None,
        "severity": None,
    }
    assert "exclude" not in request


# One slot per configured angle, so a first run uses up the whole marketing pool.
EVERY_ANGLE_SLOT = [chr(97 + i) for i in range(len(TYPES.angles("marketing-post")))]


async def test_prefilter_drops_the_weeks_taken_topic_and_recent_runs(tmp_path):
    first = TopicModels()
    first_run = await run(tmp_path, first, slots("a", "b"), "first")
    used = {slot.topic_id for slot in first_run.slots.values()}
    models = TopicModels()
    plan = slots("c", "d", "e", focus=FOCUSED_ENTRY["title"])
    await run(tmp_path, models, plan, "second")
    choices = models.topic_request()["choices"]
    assert set(choices) == {"d", "e"}
    for ids in choices.values():
        assert not used & set(ids)
        assert FOCUSED_ENTRY["id"] not in ids
        assert len(ids) == len(KB.entries) - len(used | {FOCUSED_ENTRY["id"]})


async def test_slot_with_nothing_left_after_filtering_gets_its_whole_pool(tmp_path):
    await run(tmp_path, TopicModels(), slots(*EVERY_ANGLE_SLOT, type="marketing-post"), "first")
    models = TopicModels()
    await run(tmp_path, models, slots("x", type="marketing-post"), "second")
    assert models.topic_request()["choices"]["x"] == TYPES.topic_pool("marketing-post", KB)
    assert "history_exhausted:x" in resolved(tmp_path, "second")["warnings"]


async def test_rejected_proposals_are_logged_and_code_picks_instead(tmp_path):
    models = TopicModels(
        {
            "a": ACCEPTED_ENTRY,
            "b": ACCEPTED_ENTRY,
            "c": UNKNOWN_ENTRY,
            "d": "ANGLE-service",
        }
    )
    summary = await run(tmp_path, models, slots("a", "b", "c", "d"))
    warnings = resolved(tmp_path)["warnings"]
    assert summary.slots["a"].topic_id == ACCEPTED_ENTRY
    assert f"topic_proposal_rejected:b:{ACCEPTED_ENTRY}" in warnings
    assert f"topic_proposal_rejected:c:{UNKNOWN_ENTRY}" in warnings
    assert "topic_proposal_rejected:d:ANGLE-service" in warnings
    assert f"topic_proposal_rejected:a:{ACCEPTED_ENTRY}" not in warnings
    assert summary.slots["b"].topic_id not in {ACCEPTED_ENTRY, UNKNOWN_ENTRY}
    assert summary.slots["c"].topic_id.startswith("SS-")


async def test_reasons_reach_the_resolved_plan_only_for_accepted_picks(tmp_path):
    models = TopicModels(
        {"a": ACCEPTED_ENTRY, "b": ACCEPTED_ENTRY, "c": SECOND_ACCEPTED_ENTRY},
        {"a": "  Fresh angle on upgrades.  ", "b": "Duplicate pick.", "c": "x" * 500, "z": "?"},
    )
    await run(tmp_path, models, slots("a", "b", "c"))
    plan = resolved(tmp_path)
    assert plan["topic_reasons"] == {"a": "Fresh angle on upgrades.", "c": "x" * 200}
    assert [slot["slot_id"] for slot in plan["slots"]] == ["a", "b", "c"]


async def test_plan_without_reasons_still_resolves_and_old_files_still_load(tmp_path):
    await run(tmp_path, TopicModels({"a": ACCEPTED_ENTRY}), slots("a"))
    plan = resolved(tmp_path)
    assert plan["topic_reasons"] == {}
    from content_pipeline.contracts import ResolvedPlan

    legacy = {key: value for key, value in plan.items() if key != "topic_reasons"}
    assert ResolvedPlan.model_validate(legacy).topic_reasons == {}


async def test_failed_topic_call_falls_back_to_code_with_no_reasons(tmp_path):
    models = TopicModels({"a": SECOND_ACCEPTED_ENTRY}, {"a": "never used"}, fail=True)
    summary = await run(tmp_path, models, slots("a", "b"))
    plan = resolved(tmp_path)
    assert "topic_selection_failed:code_fallback" in plan["warnings"]
    assert plan["topic_reasons"] == {}
    assert summary.shipped == 2
    # The ranked fallback ran; the scripted id was never read.
    assert summary.slots["a"].topic_id != SECOND_ACCEPTED_ENTRY
    assert not any(warning.startswith("topic_proposal_rejected") for warning in plan["warnings"])


async def test_model_is_asked_through_a_schema_with_one_named_field_per_open_slot(tmp_path):
    # Live finding: a free-form dict[str, str] reaches Claude as an object with no properties
    # and it answers {}. The old call was a silent no-op; named fields give it something to fill.
    models = TopicModels({"b": ACCEPTED_ENTRY})
    await run(tmp_path, models, slots("a", "b", "c", focus=FOCUSED_ENTRY["title"]))
    (schema,) = models.schemas
    for name in ("TopicAssignments", "TopicReasons"):
        assert set(schema["$defs"][name]["properties"]) == {"b", "c"}


def test_wire_schema_keeps_odd_slot_ids_and_folds_to_the_canonical_contract():
    request = TopicChoiceRequest(choices={"thread 1/a": ["SS-01"], "b": ["SS-02"], "c": ["SS-03"]})
    wire = topic_wire_schema(request).model_validate(
        {
            "slot_id": "_batch",
            "attempt": 1,
            "assignments": {"thread 1/a": "SS-01", "b": None, "unknown": "SS-09"},
            "reasons": {"thread 1/a": "Fits.", "b": ""},
        }
    )
    chosen = canonical_topic_choices(wire)
    assert chosen.assignments == {"thread 1/a": "SS-01"}
    assert chosen.reasons == {"thread 1/a": "Fits."}
    empty = topic_wire_schema(request).model_validate(
        {"slot_id": "_batch", "attempt": 1, "assignments": {}}
    )
    assert canonical_topic_choices(empty).assignments == {}


async def test_reason_is_dropped_when_the_pick_was_rejected_even_if_fallback_lands_on_it(tmp_path):
    marketing_id = TYPES.topic_pool("marketing-post", KB)[0]
    await run(tmp_path, TopicModels(), slots(*EVERY_ANGLE_SLOT, type="marketing-post"), "first")
    # Every angle is now recent history, so the proposed id is refused; the fallback then happens
    # to choose that same id first. The model's reason must not be credited with the pick.
    models = TopicModels({"x": marketing_id}, {"x": "Service fits."})
    summary = await run(tmp_path, models, slots("x", type="marketing-post"), "second")
    plan = resolved(tmp_path, "second")
    assert summary.slots["x"].topic_id == marketing_id
    assert f"topic_proposal_rejected:x:{marketing_id}" in plan["warnings"]
    assert plan["topic_reasons"] == {}


async def test_an_answer_that_skips_a_slot_is_warned_not_silent(tmp_path):
    models = TopicModels({"a": ACCEPTED_ENTRY})
    summary = await run(tmp_path, models, slots("a", "b", "c"))
    warnings = resolved(tmp_path)["warnings"]
    assert summary.shipped == 3
    assert "topic_proposal_missing:b" in warnings
    assert "topic_proposal_missing:c" in warnings
    assert "topic_proposal_missing:a" not in warnings


async def test_a_failed_call_reports_only_the_failure_not_a_missing_pick_per_slot(tmp_path):
    await run(tmp_path, TopicModels(fail=True), slots("a", "b"))
    warnings = resolved(tmp_path)["warnings"]
    assert "topic_selection_failed:code_fallback" in warnings
    assert not any(warning.startswith("topic_proposal_missing") for warning in warnings)


def test_a_null_reasons_object_is_tolerated():
    request = TopicChoiceRequest(choices={"a": ["SS-01"]})
    wire = topic_wire_schema(request).model_validate(
        {"slot_id": "_batch", "attempt": 1, "assignments": {"a": "SS-01"}, "reasons": None}
    )
    chosen = canonical_topic_choices(wire)
    assert chosen.assignments == {"a": "SS-01"} and chosen.reasons == {}
