"""Task M coverage for data-loaded marketing formats and the weekly mix."""

import re
import shutil
from datetime import datetime, timedelta, timezone
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

    pipeline = mix_pipeline(fake_types)
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = pack(state.slot.slot_id).model_copy(update={"topic_name": state.slot.topic_id})
    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert slots[2].outcome.value == "failed_research"
    assert slots[2].reasons == ["weekly_mix_similar_topic"]
    assert any("m3:similar_topic:fail" in warning for warning in warnings)
    assert slots[0].outcome is None
    assert slots[1].outcome.value == "failed_research"


def test_weekly_mix_drops_a_duplicate_resolved_topic():
    slots = states("ANGLE-web3-security", "ANGLE-web3-security")

    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = pack(state.slot.slot_id).model_copy(update={"topic_name": "same topic"})
    warnings = pipeline._enforce_marketing_mix(slots, "viable")

    assert slots[1].outcome.value == "failed_research"
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


def test_interrupted_legacy_marketing_snapshot_without_later_inputs_keeps_legacy_format(tmp_path):
    """A pre-format snapshot resumes with its old one-unit marketing contract."""
    root = tmp_path / "legacy_marketing_snapshot"
    shutil.copytree(ROOT / "post_types", root / "post_types")
    shutil.copytree(ROOT / "agents", root / "agents")
    type_path = root / "post_types/marketing-post/type.yaml"
    type_path.write_text(
        "\n".join(
            line
            for line in type_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("formats:")
            and not line.startswith("- id:")
            and not line.startswith("  units:")
            and not line.startswith("  link_rule:")
            and not line.startswith("  length_hint:")
            and not line.startswith("  numbering:")
            and not line.startswith("weekly_topic_similarity:")
        )
        + "\n",
        encoding="utf-8",
    )
    legacy_types = PostTypes(root)
    slot = ResolvedSlot(slot_id="legacy", type="marketing-post", format="short-tip")

    assert legacy_types.spec("marketing-post").formats == []
    assert legacy_types.format(slot) is None
    assert mix_pipeline(legacy_types)._assign_marketing_mix([SlotState(slot=slot)]) == []


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


@pytest.mark.parametrize(
    "text,allowed,expected",
    [
        ("Book a review. DM us.", True, ["marketing_cta_limit:2"]),
        ("Check your approvals before signing.", False, []),
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
