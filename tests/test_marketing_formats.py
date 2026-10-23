"""Task M coverage for data-loaded marketing formats and the weekly mix."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_pipeline.config import load_brand, load_type
from content_pipeline.contracts import Angle, SlotState
from content_pipeline.models import Draft, ResolvedSlot, ResearchPack, SourceRecord, Tweet
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


def mix_pipeline(types=None):
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.types = types or PostTypes(ROOT)
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
    fake_types = SimpleNamespace(
        spec=real.spec,
        angles=lambda _: [*real.angles("marketing-post"), extra],
    )
    slots = states(
        "ANGLE-agentic-security",
        "ANGLE-vulnerability-class",
        "ANGLE-agentic-security-extra",
    )

    warnings = mix_pipeline(fake_types)._assign_marketing_mix(slots)

    assert slots[2].outcome.value == "failed_research"
    assert slots[2].reasons == ["weekly_mix_service_repeat"]
    assert warnings == ["weekly_mix_drop_service:m3"]
    assert all(slot.outcome is None for slot in slots[:2])


def test_weekly_mix_drops_a_duplicate_resolved_topic():
    slots = states("ANGLE-web3-security", "ANGLE-web3-security")

    warnings = mix_pipeline()._assign_marketing_mix(slots)

    assert slots[1].outcome.value == "failed_research"
    assert slots[1].reasons == ["weekly_mix_similar_topic"]
    assert warnings == ["weekly_mix_drop_similar:m2"]


def test_brand_voice_has_no_placeholder_or_numeric_firm_figures():
    text = (ROOT / "inputs/brand-voice.md").read_text(encoding="utf-8")

    assert "Sentinel Grove" not in text
    assert all(not re.search(r"\b\d+\b", statement) for statement in RULES.approved_stats.values())
    assert RULES.brand_name == "SanadSquad"
