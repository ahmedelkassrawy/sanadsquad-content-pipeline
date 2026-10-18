"""Regression tests for the third evaluation: approved stats stay separate facts, research
failures say why, and marketing angles no longer share a firm description."""

from pathlib import Path
from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, ResolvedSlot, SourceRecord, Tweet
from content_pipeline.post_types import PostTypes
from content_pipeline.research import KnowledgeBase
from content_pipeline.validation import (
    check_stat_claims,
    is_writer_note,
    stat_feedback,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def stat_pack(ids=("STAT-01", "STAT-02", "STAT-03", "STAT-05")):
    return ResearchPack(
        slot_id="marketing_2",
        topic_id="ANGLE-credibility",
        topic_name="credibility",
        sources=[
            SourceRecord(
                source_id=id,
                kind="stat",
                title="Sentinel Grove",
                evidence_text=RULES.approved_stats[id],
            )
            for id in ids
        ],
    )


def test_two_stats_joined_in_one_sentence_are_rejected():
    # The sentence the evaluator flagged in the round-2 sample.
    text = "12 smart contract audits completed, by a team of 3 auditors."

    assert check_stat_claims(text, stat_pack()) == ["joined_stats:STAT-01+STAT-02"]


def test_a_stat_number_moved_to_another_noun_is_rejected():
    assert check_stat_claims("Sentinel Grove has 12 auditors.", stat_pack()) == [
        "stat_misbound:12 auditors"
    ]


def test_each_stat_in_its_own_sentence_passes():
    text = "12 smart contract audits completed. Sentinel Grove has 3 auditors. Request a review."

    assert check_stat_claims(text, stat_pack()) == []


def test_packs_without_stats_and_numbers_in_code_are_not_checked():
    thread = ResearchPack(
        slot_id="thread_1",
        topic_id="KB-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-01", kind="kb", title="Reentrancy", evidence_text="12 calls, 3 steps."
            )
        ],
    )

    assert check_stat_claims("12 calls and 3 steps in one sentence.", thread) == []
    assert check_stat_claims("Set `x = 12; y = 3;` once.", stat_pack()) == []


def test_joined_stats_fail_validation_with_a_writer_note():
    draft = Draft(
        slot_id="marketing_2",
        tweets=[
            Tweet(
                text="12 smart contract audits completed, by a team of 3 auditors.",
                source_ids=["STAT-01", "STAT-02"],
            )
        ],
    )

    result = validate(draft, stat_pack(), load_type(ROOT, "marketing-post"), RULES)
    notes = stat_feedback(result)

    assert "tweet_1:joined_stats:STAT-01+STAT-02" in result.violations
    assert len(notes) == 1 and "its own sentence" in notes[0]
    assert is_writer_note(notes[0])
    assert not is_writer_note("tweet_1:joined_stats:STAT-01+STAT-02")


def test_only_credibility_describes_the_firm():
    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    firm = {}

    for angle in types.angles("marketing-post"):
        slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id=angle.id)
        ids = {s.source_id for s in types.assigned_pack(slot, kb).sources}
        firm[angle.name] = "STAT-03" in ids

        # Every angle can still invite a review.
        assert "STAT-05" in ids, angle.id

    assert firm == {"service": False, "credibility": True, "education-to-cta": False}


async def test_research_failures_say_why():
    from content_pipeline.model_client import ModelFailure
    from content_pipeline.pipeline import describe_error

    error = ModelFailure("failed_research", "step_limit_reached_without_supporting_evidence")

    assert describe_error(error) == (
        "ModelFailure: failed_research (step_limit_reached_without_supporting_evidence)"
    )
