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
    offer = {}

    for angle in types.angles("marketing-post"):
        slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id=angle.id)
        ids = {s.source_id for s in types.assigned_pack(slot, kb).sources}
        firm[angle.name] = "STAT-03" in ids
        offer[angle.name] = "STAT-05" in ids

    assert firm == {"service": False, "credibility": True, "education-to-cta": False}
    # The education post ends on its own question, so only two posts carry the review offer.
    assert offer == {"service": True, "credibility": True, "education-to-cta": False}


def test_an_empty_stat_list_means_the_angle_uses_no_firm_stats():
    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id="ANGLE-education")

    kinds = {s.kind for s in types.assigned_pack(slot, kb).sources}

    assert kinds == {"kb"}


async def test_research_failures_say_why():
    from content_pipeline.model_client import ModelFailure
    from content_pipeline.pipeline import describe_error

    error = ModelFailure("failed_research", "step_limit_reached_without_supporting_evidence")

    assert describe_error(error) == (
        "ModelFailure: failed_research (step_limit_reached_without_supporting_evidence)"
    )


def test_natural_rewordings_of_a_joined_or_misbound_stat_are_caught():
    pack = stat_pack()

    assert check_stat_claims(
        "Our three auditors have completed 12 smart contract audits.", pack
    ) == ["joined_stats:STAT-01+STAT-02"]
    assert check_stat_claims(
        "Sentinel Grove has completed twelve smart contract audits with 3 auditors.", pack
    ) == ["joined_stats:STAT-01+STAT-02"]
    assert check_stat_claims("Sentinel Grove has 12 smart contract auditors.", pack) == [
        "stat_misbound:12 smart"
    ]


def test_reordered_or_linked_stat_numbers_are_not_false_positives():
    pack = stat_pack()

    for text in [
        "12 completed smart contract audits.",
        "A 3-auditor team.",
        "See https://example.com/12 for details.",
    ]:
        assert check_stat_claims(text, pack) == [], text


def test_twice_in_a_thread_needs_evidence_that_says_twice():
    pack = ResearchPack(
        slot_id="thread_1",
        topic_id="KB-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-01",
                kind="kb",
                title="Reentrancy",
                evidence_text="A callback can withdraw again against the unchanged balance.",
            )
        ],
    )
    texts = [f"Point {n}: the callback can withdraw twice here." for n in "ABCDE"]
    draft = Draft(slot_id="thread_1", tweets=[Tweet(text=t, source_ids=["KB-01"]) for t in texts])

    result = validate(draft, pack, load_type(ROOT, "tech-thread"), RULES)

    assert "tweet_1:unsupported_claim_word:twice" in result.violations
