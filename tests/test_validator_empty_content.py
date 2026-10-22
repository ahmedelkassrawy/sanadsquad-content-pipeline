"""Validator rejects blank, hashtag-only and otherwise empty thread content."""

from pathlib import Path
import pytest
from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.validation import validate

ROOT = Path(__file__).resolve().parents[1]


def validate_texts(texts, post_type="tech-thread"):
    draft = Draft(slot_id="slot", tweets=[Tweet(text=text, source_ids=["SS-01"]) for text in texts])
    pack = ResearchPack(
        slot_id="slot",
        topic_id="SS-01",
        topic_name="Topic",
        sources=[
            SourceRecord(
                source_id="SS-01", kind="kb", title="Topic", evidence_text="Source evidence."
            )
        ],
    )
    return validate(
        draft, pack, load_type(ROOT, post_type), load_brand(ROOT / "inputs/brand-voice.md")[0]
    )


def test_all_blank_thread_cannot_be_validated_by_numbering():
    result = validate_texts([" "] * 5)
    assert result.accepted is False
    assert result.violations == [f"tweet_{i}:invalid_text" for i in range(1, 6)]
    assert [tweet.text for tweet in result.normalized.tweets] == [f"{i}/5 " for i in range(1, 6)]
    assert [tweet.text for tweet in result.original.tweets] == [" "] * 5
    assert result.fixes == [f"tweet_{i}:whitespace" for i in range(1, 6)]


def test_one_blank_unit_rejects_otherwise_nonempty_thread():
    texts = [
        "State before calls.",
        "Explain the root cause.",
        " \t ",
        "Check the invariant.",
        "Review the call order.",
    ]
    result = validate_texts(texts)
    assert result.accepted is False
    assert result.violations == ["tweet_3:invalid_text"]
    assert [tweet.text for tweet in result.normalized.tweets] == [
        "1/5 State before calls.",
        "2/5 Explain the root cause.",
        "3/5 ",
        "4/5 Check the invariant.",
        "5/5 Review the call order.",
    ]
    assert [tweet.text for tweet in result.original.tweets] == texts
    assert result.fixes == ["tweet_3:whitespace"]


@pytest.mark.parametrize("post_type,count", [("tech-thread", 5), ("marketing-post", 1)])
def test_hashtag_only_units_become_empty_and_fail_before_numbering(post_type, count):
    result = validate_texts([" #audit #security "] * count, post_type)
    assert result.accepted is False
    assert result.violations == [f"tweet_{i}:invalid_text" for i in range(1, count + 1)]
    assert [tweet.text for tweet in result.normalized.tweets] == (
        [f"{i}/5 " for i in range(1, 6)] if count == 5 else [""]
    )
    assert [tweet.text for tweet in result.original.tweets] == [" #audit #security "] * count
    assert result.fixes == [
        fix
        for i in range(1, count + 1)
        for fix in [f"tweet_{i}:whitespace", f"tweet_{i}:trailing_hashtags"]
    ]


def test_valid_normalized_body_still_has_full_numbered_length_checked():
    result = validate_texts(["a" * 277] * 5)
    assert result.accepted is False
    assert [tweet.text for tweet in result.normalized.tweets] == [
        f"{i}/5 " + "a" * 277 for i in range(1, 6)
    ]
    assert [error for error in result.violations if error.endswith(":length")] == [
        f"tweet_{i}:length" for i in range(1, 6)
    ]
