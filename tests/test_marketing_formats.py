"""Task M coverage for data-loaded marketing formats and the weekly mix."""

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from content_pipeline.config import load_brand, load_type
from content_pipeline.contracts import Angle, RunIdentity, SlotState
from content_pipeline.models import (
    Draft,
    ResolvedSlot,
    ResearchPack,
    SlotOutcome,
    SourceRecord,
    Tweet,
)
from content_pipeline.pipeline import Pipeline
from content_pipeline.post_types import PostTypes
from content_pipeline.validation import validate


ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]
SPEC = load_type(ROOT, "marketing-post")


def pack(slot_id="marketing"):
    return ResearchPack(
        slot_id=slot_id,
        topic_id="ANGLE-web3-security",
        topic_name="web3 security",
        sources=[
            SourceRecord(
                source_id="WEB-abcd",
                kind="web",
                url="https://example.com/review",
                title="Review guidance",
                evidence_text="Security review covers the whole attack surface.",
            )
        ],
    )


def draft_for(format, *, valid=True):
    count = format.units.min if valid else format.units.max + 1
    texts = [
        "Security review covers the whole attack surface.",
        "Infrastructure changes the production path.",
        "Agent tools need a spending policy.",
        "Payment rails need clear integration boundaries.",
        "A protocol also depends on its frontend.",
        "A review can examine the surrounding systems.",
        "Evidence keeps the scope concrete.",
        "Another detail exceeds the requested format.",
    ][:count]
    if format.link_rule == "required" and valid:
        texts[-1] += " https://example.com/review"
    if format.link_rule == "forbidden" and not valid:
        texts[0] += " https://example.com/review"
    return Draft(
        slot_id="marketing",
        tweets=[Tweet(text=text, source_ids=["WEB-abcd"]) for text in texts],
    )


@pytest.mark.parametrize("format", SPEC.formats, ids=lambda format: format.id)
def test_each_format_accepts_its_valid_draft(format):
    result = validate(draft_for(format), pack(), SPEC, RULES, format=format)

    assert result.accepted, result.violations
    if format.numbering:
        assert [tweet.text[:3] for tweet in result.normalized.tweets] == ["1/3", "2/3", "3/3"]


@pytest.mark.parametrize("format", SPEC.formats, ids=lambda format: format.id)
def test_each_format_rejects_an_invalid_draft(format):
    result = validate(draft_for(format, valid=False), pack(), SPEC, RULES, format=format)

    assert not result.accepted
    if format.link_rule == "forbidden":
        assert f"format_link_forbidden:{format.id}" in result.violations
    else:
        assert any(
            violation.startswith(f"format_units:{format.id}:") for violation in result.violations
        )


@pytest.mark.parametrize(
    "format",
    [format for format in SPEC.formats if format.link_rule == "required"],
    ids=lambda format: format.id,
)
def test_required_link_format_rejects_a_valid_length_draft_without_a_link(format):
    draft = draft_for(format)
    draft.tweets[-1].text = draft.tweets[-1].text.split(" https://", 1)[0]

    result = validate(draft, pack(), SPEC, RULES, format=format)

    assert f"format_link_required:{format.id}" in result.violations


def states(*topic_ids):
    return [
        SlotState(slot=ResolvedSlot(slot_id=f"m{index}", type="marketing-post", topic_id=topic_id))
        for index, topic_id in enumerate(topic_ids, 1)
    ]


def stub_types(real, angles):
    """A PostTypes stand-in whose angle table has extra test angles."""
    stub = SimpleNamespace(spec=real.spec, angles=lambda _: angles)
    stub.marketing_angle = lambda slot: PostTypes.marketing_angle(stub, slot)
    return stub


def mix_pipeline(types=None, week="2024-W02"):
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.types = types or PostTypes(ROOT)
    pipeline.context = SimpleNamespace(
        identity=RunIdentity(
            thread_id="test-thread",
            run_id="test-run",
            snapshot_files={},
            marketing_week=week,
        )
    )
    return pipeline


def test_weekly_mix_assigns_different_formats_and_both_link_shapes():
    slots = states("ANGLE-web3-security", "ANGLE-pentesting", "ANGLE-agentic-security")
    warnings = mix_pipeline()._assign_marketing_mix(slots)

    assert warnings == []
    assert [slot.slot.format for slot in slots] == [
        "short-tip",
        "learning-resource",
        "differentiation",
    ]
    assert [slot.slot.cta_allowed for slot in slots] == [True, False, False]


def assigned_formats(week, *topic_ids):
    slots = states(*topic_ids)
    mix_pipeline(week=week)._assign_marketing_mix(slots)
    return [slot.slot.format for slot in slots]


def test_marketing_format_rotation_is_deterministic_and_saved_with_the_run_week():
    topics = ("ANGLE-web3-security", "ANGLE-pentesting", "ANGLE-agentic-security")
    first_week = assigned_formats("2026-W01", *topics)
    next_week = assigned_formats("2026-W02", *topics)

    assert first_week != next_week
    assert assigned_formats("2026-W01", *topics) == first_week

    resumed_identity = RunIdentity.model_validate_json(
        json.dumps(
            RunIdentity(
                thread_id="test-thread",
                run_id="test-run",
                snapshot_files={},
                marketing_week="2026-W01",
            ).model_dump()
        )
    )
    resumed = mix_pipeline(week=resumed_identity.marketing_week)
    resumed_slots = states(*topics)
    resumed._assign_marketing_mix(resumed_slots)

    assert [slot.slot.format for slot in resumed_slots] == first_week


@pytest.mark.parametrize("week", [f"2026-W{number:02d}" for number in range(1, 7)])
@pytest.mark.parametrize("count", [2, 3])
def test_every_rotation_offset_preserves_required_and_forbidden_link_shapes(week, count):
    formats = assigned_formats(
        week,
        *("ANGLE-web3-security", "ANGLE-pentesting", "ANGLE-agentic-security")[:count],
    )
    by_id = {format.id: format for format in SPEC.formats}

    assert any(by_id[format].link_rule == "required" for format in formats)
    assert any(by_id[format].link_rule == "forbidden" for format in formats)


def test_agentic_payments_prefers_the_thread_format():
    assert assigned_formats("2026-W01", "ANGLE-agentic-payments") == ["thread"]


def test_unknown_format_order_id_fails_when_the_type_loads(tmp_path):
    root = tmp_path / "invalid-format-order"
    shutil.copytree(ROOT / "post_types", root / "post_types")
    shutil.copytree(ROOT / "agents", root / "agents")
    path = root / "post_types/marketing-post/type.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["format_order"] = ["short-tip", "not-a-format"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown marketing format in format_order"):
        load_type(root, "marketing-post")


def test_weekly_mix_allows_one_service_repeat_only_as_news_reaction():
    slots = states("ANGLE-agentic-security", "ANGLE-vulnerability-class")

    warnings = mix_pipeline()._assign_marketing_mix(slots)

    assert warnings == []
    assert slots[1].slot.format == "news-reaction"
    assert all(slot.outcome is None for slot in slots)


def test_weekly_mix_drops_a_third_service_repeat_without_stopping_other_slots():
    real = PostTypes(ROOT)
    extra = Angle(
        id="ANGLE-agentic-security-extra",
        name="agentic-security-extra",
        service_line="agentic-security",
        stat_ids=["STAT-03"],
    )
    fake_types = stub_types(real, [*real.angles("marketing-post"), extra])
    slots = states(
        "ANGLE-agentic-security",
        "ANGLE-vulnerability-class",
        "ANGLE-agentic-security-extra",
    )

    pipeline = mix_pipeline(fake_types)
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = pack(state.slot.slot_id).model_copy(update={"topic_name": state.slot.topic_id})
    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert slots[2].outcome.value == "dropped_weekly_mix"
    assert slots[2].reasons == ["weekly_mix_similar_topic"]
    assert any("m3:similar_topic:fail" in warning for warning in warnings)
    assert slots[0].outcome is None
    assert slots[1].outcome.value == "dropped_weekly_mix"


def test_weekly_mix_drops_a_duplicate_resolved_topic():
    slots = states("ANGLE-web3-security", "ANGLE-web3-security")

    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = pack(state.slot.slot_id).model_copy(update={"topic_name": "same topic"})
    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert slots[1].outcome.value == "dropped_weekly_mix"
    assert slots[1].reasons == ["weekly_mix_similar_topic"]
    assert any("m2:similar_topic:fail" in warning for warning in warnings)


def test_weekly_mix_allows_a_repeated_service_only_with_recent_dated_news():
    slots = states("ANGLE-agentic-security", "ANGLE-vulnerability-class")
    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = pack(state.slot.slot_id).model_copy(update={"topic_name": state.slot.topic_id})
    slots[1].pack.sources[0].published_at = datetime.now(timezone.utc) - timedelta(days=1)

    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert all(state.outcome is None for state in slots)
    assert any("m2:service_repeat:pass" in warning for warning in warnings)


def test_legacy_snapshot_without_format_order_keeps_the_original_offset_zero_behavior(tmp_path):
    """A snapshot before format_order resumes its original hard-coded order at offset zero."""
    root = tmp_path / "legacy_marketing_snapshot"
    shutil.copytree(ROOT / "post_types", root / "post_types")
    shutil.copytree(ROOT / "agents", root / "agents")
    shutil.copytree(ROOT / "inputs", root / "inputs")
    type_path = root / "post_types/marketing-post/type.yaml"
    data = yaml.safe_load(type_path.read_text(encoding="utf-8"))
    data.pop("format_order")
    type_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    legacy_types = PostTypes(root)
    slots = [
        SlotState(
            slot=ResolvedSlot(slot_id=f"legacy-{index}", type="marketing-post", topic_id=topic)
        )
        for index, topic in enumerate(
            ("ANGLE-web3-security", "ANGLE-pentesting", "ANGLE-agentic-security"), 1
        )
    ]

    assert mix_pipeline(legacy_types, week=None)._assign_marketing_mix(slots) == []
    assert [state.slot.format for state in slots] == [
        "short-tip",
        "learning-resource",
        "differentiation",
    ]


def test_brand_voice_has_no_placeholder_or_numeric_firm_figures():
    text = (ROOT / "inputs/brand-voice.md").read_text(encoding="utf-8")

    assert "Sentinel Grove" not in text
    assert all(not re.search(r"\b\d+\b", statement) for statement in RULES.approved_stats.values())
    assert RULES.brand_name == "SanadSquad"


def test_marketing_requires_a_cited_fetched_web_source_even_with_a_stat_or_kb():
    draft = Draft(
        slot_id="marketing",
        tweets=[
            Tweet(text="Security review covers the whole attack surface.", source_ids=["STAT-01"])
        ],
    )
    sources = [
        *pack().sources,
        SourceRecord(
            source_id="STAT-01",
            kind="stat",
            title="Service statement",
            evidence_text="Security review covers the whole attack surface.",
        ),
    ]
    result = validate(
        draft,
        ResearchPack(
            slot_id="marketing", topic_id="ANGLE-web3-security", topic_name="web3", sources=sources
        ),
        SPEC,
        RULES,
        format=next(format for format in SPEC.formats if format.id == "short-tip"),
    )

    assert "marketing_web_citation_required" in result.violations


def test_marketing_requires_a_cited_fetched_web_source_without_a_format_argument():
    draft = Draft(
        slot_id="marketing",
        tweets=[
            Tweet(text="Security review covers the whole attack surface.", source_ids=["STAT-01"])
        ],
    )
    sources = [
        *pack().sources,
        SourceRecord(
            source_id="STAT-01",
            kind="stat",
            title="Service statement",
            evidence_text="Security review covers the whole attack surface.",
        ),
    ]

    result = validate(
        draft,
        ResearchPack(
            slot_id="marketing", topic_id="ANGLE-web3-security", topic_name="web3", sources=sources
        ),
        SPEC,
        RULES,
    )

    assert "marketing_web_citation_required" in result.violations


@pytest.mark.parametrize(
    "text,allowed,expected",
    [
        ("Book a review. DM us.", True, ["marketing_cta_limit:2"]),
        ("Check your approvals before signing.", False, []),
        ("If the agent cannot reach out to the network, egress is constrained.", False, []),
        ("Contact us for a review.", False, ["weekly_mix_cta_not_allowed"]),
    ],
)
def test_ctas_are_data_driven_and_counted_across_one_post(text, allowed, expected):
    result = validate(
        Draft(slot_id="marketing", tweets=[Tweet(text=text, source_ids=["WEB-abcd"])]),
        pack(),
        SPEC,
        RULES,
        cta_allowed=allowed,
        ai_policy_root=ROOT,
    )

    assert [value for value in result.violations if "cta" in value] == expected


def test_weekly_mix_leaves_link_diversity_to_planning_for_one_viable_slot():
    slots = states("ANGLE-web3-security")
    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    slots[0].pack = pack(slots[0].slot.slot_id)

    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert slots[0].outcome is None
    assert not any("link_diversity" in warning for warning in warnings)


def test_shipped_link_diversity_is_logged_as_unmet_without_dropping_the_post():
    """The accepted week-2 learning resource stays shipped if its short tip was lost earlier."""
    slots = states("ANGLE-agentic-payments", "ANGLE-agentic-security")
    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    slots[0].pack = pack(slots[0].slot.slot_id)
    slots[0].outcome = SlotOutcome.dropped_quality
    slots[1].pack = pack(slots[1].slot.slot_id)
    slots[1].draft = Draft(
        slot_id=slots[1].slot.slot_id,
        tweets=[
            Tweet(
                text=(
                    "A permission check that only fires when a balance changes misses everything "
                    "else. In one Solana finding, a role limited to a token amount could still call "
                    "SetAuthority and take over the token account."
                ),
                source_ids=["WEB-abcd"],
            ),
            Tweet(
                text=(
                    "OWASP's page on excessive agency makes the same point for AI agents: enforce "
                    "tool authorization independently. https://example.com/review"
                ),
                source_ids=["WEB-abcd"],
            ),
        ],
    )
    slots[1].outcome = SlotOutcome.shipped

    warnings = pipeline._enforce_marketing_mix(slots, "shipped")

    assert slots[1].outcome is SlotOutcome.shipped
    assert "weekly_mix:shipped:set:link_diversity:unmet:no_unlinked_post_shipped" in warnings


async def test_weekly_mix_cta_failure_gets_the_weekly_mix_outcome():
    state = SlotState(
        slot=ResolvedSlot(slot_id="marketing", type="marketing-post"),
        attempt=2,
        feedback=["weekly_mix_cta_not_allowed"],
    )

    result = await Pipeline.__new__(Pipeline).retry(state.model_dump(mode="json"))

    assert result["outcome"] == "dropped_weekly_mix"
    assert result["reasons"] == ["weekly_mix_cta_not_allowed"]


def test_weekly_mix_rejects_a_third_current_news_service_repeat():
    real = PostTypes(ROOT)
    extras = [
        Angle(
            id=f"ANGLE-agentic-security-news-{index}",
            name=f"agentic security news {index}",
            service_line="agentic-security",
            stat_ids=["STAT-03"],
        )
        for index in range(1, 4)
    ]
    fake_types = stub_types(real, extras)
    slots = states(*(angle.id for angle in extras))
    pipeline = mix_pipeline(fake_types)
    pipeline._assign_marketing_mix(slots)
    for index, state in enumerate(slots, 1):
        state.pack = pack(state.slot.slot_id).model_copy(
            update={
                "topic_name": ("runtime isolation", "payment policy", "browser extension")[
                    index - 1
                ]
            }
        )
        state.pack.sources[0].published_at = datetime.now(timezone.utc) - timedelta(days=1)

    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert slots[0].outcome is None
    assert slots[1].outcome is None
    assert slots[2].outcome.value == "dropped_weekly_mix"
    assert slots[2].reasons == ["weekly_mix_service_repeat"]
    assert any("m3:service_repeat:fail" in warning for warning in warnings)


def test_weekly_mix_counts_shipped_ctas_not_planned_cta_allowances():
    slots = states(
        "ANGLE-web3-security",
        "ANGLE-pentesting",
        "ANGLE-agentic-security",
        "ANGLE-agentic-payments",
    )
    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = pack(state.slot.slot_id).model_copy(update={"topic_name": state.slot.topic_id})
        state.draft = Draft(
            slot_id=state.slot.slot_id,
            tweets=[
                Tweet(
                    text="A technical observation without a commercial ask.",
                    source_ids=["WEB-abcd"],
                )
            ],
        )
        state.outcome = SlotOutcome.shipped
    slots[1].slot.cta_allowed = True  # A six-slot plan may have retained two allowances.

    pipeline._enforce_marketing_mix(slots, "shipped")

    assert all(state.outcome.value == "shipped" for state in slots)
