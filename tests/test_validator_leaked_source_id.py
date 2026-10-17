"""Validator rejects drafts that leak a research pack source id."""

from pathlib import Path

import pytest

from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.validation import validate


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rules():
    return load_brand(ROOT / "inputs/brand-voice.md")[0]


def check(text, rules, source_id="STAT-03", status="ok", post_type="marketing-post"):
    spec = load_type(ROOT, post_type)
    draft = Draft(
        slot_id="s12",
        tweets=[Tweet(text="Update state before external calls.", source_ids=["KB-99"])]
        * (spec.units.min - 1)
        + [Tweet(text=text, source_ids=["KB-99"])],
    )
    pack = ResearchPack(
        slot_id="s12",
        topic_id="KB-99",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-99",
                kind="kb",
                title="Reentrancy",
                evidence_text="Update state before external calls.",
            ),
            SourceRecord(
                source_id=source_id,
                kind={"KB": "kb", "STAT": "stat", "WEB": "web"}[source_id.split("-")[0]],
                title="Supporting evidence",
                evidence_text="Teams can request a review." if status == "ok" else "",
                status=status,
            ),
        ],
    )
    return validate(draft, pack, spec, rules)


@pytest.mark.parametrize("status", ["ok", "snippet_only", "fetch_failed"])
def test_parenthesized_pack_source_id_is_rejected_without_fixing(rules, status):
    text = "Our demo service (STAT-03) lets teams request a review."
    result = check(text, rules, status=status)

    assert not result.accepted
    assert result.violations == ["tweet_1:leaked_source_id:STAT-03"]
    assert result.normalized.tweets[0].text == text
    assert result.original.tweets[0].text == text
    assert result.fixes == []


def test_bare_source_id_in_later_thread_tweet_is_rejected_after_normalization(rules):
    text = "  Review\tKB-10 before calling.  "
    result = check(text, rules, source_id="KB-10", post_type="tech-thread")

    assert not result.accepted
    assert result.violations == ["tweet_5:leaked_source_id:KB-10"]
    assert result.normalized.tweets[4].text == "5/5 Review KB-10 before calling."
    assert result.original.tweets[4].text == text
    assert result.fixes == ["tweet_5:whitespace"]


@pytest.mark.parametrize(
    "source_id,text",
    [
        ("KB-10", "Review KB-1 before calling."),
        ("KB-1", "Review KB-10 before calling."),
        ("STAT-03", "Review EIP-6780 before calling."),
        ("STAT-03", "Update state before external calls."),
        ("STAT-03", "Review stat-03 before calling."),
        ("STAT-03", "Review xSTAT-03 before calling."),
        ("STAT-03", "Review STAT-03x before calling."),
        ("STAT-03", "Review 1STAT-03 before calling."),
        ("STAT-03", "Review STAT-031 before calling."),
        ("STAT-03", "Review _STAT-03 before calling."),
        ("STAT-03", "Review STAT-03_ before calling."),
        ("STAT-03", "Review -STAT-03 before calling."),
        ("STAT-03", "Review STAT-03- before calling."),
        ("STAT-03", "Review \u00e9STAT-03 before calling."),
        ("STAT-03", "Review STAT-03\u00e9 before calling."),
    ],
)
def test_text_without_a_whole_pack_source_id_is_accepted(rules, source_id, text):
    result = check(text, rules, source_id=source_id)

    assert result.accepted, result.violations
    assert result.violations == []
    assert result.normalized.tweets[0].text == text
    assert result.fixes == []


def test_every_leaked_pack_source_id_is_reported_once_per_tweet(rules):
    result = check("Review KB-99, WEB-ab12, and KB-99.", rules, source_id="WEB-ab12")

    assert not result.accepted
    assert result.violations == [
        "tweet_1:leaked_source_id:KB-99",
        "tweet_1:leaked_source_id:WEB-ab12",
    ]
    assert result.fixes == []
