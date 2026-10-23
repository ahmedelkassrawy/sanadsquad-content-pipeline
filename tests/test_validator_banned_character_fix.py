"""Validator punctuation normalization that remains safe after AI-text checks."""

from pathlib import Path

import pytest

from content_pipeline.config import load_brand, load_type
from content_pipeline.models import BrandRules, Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.validation import validate


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rules():
    return load_brand(ROOT / "inputs/brand-voice.md")[0]


def check(text: str, rules: BrandRules, post_type="marketing-post"):
    spec = load_type(ROOT, post_type)
    source_id = "WEB-abcdef12" if post_type == "marketing-post" else "SS-01"
    draft = Draft(
        slot_id="s9",
        tweets=[Tweet(text=text, source_ids=[source_id])]
        + [
            Tweet(text="Update state before external calls.", source_ids=[source_id])
            for _ in range(spec.units.min - 1)
        ],
    )
    pack = ResearchPack(
        slot_id="s9",
        topic_id="SS-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id=source_id,
                kind="web" if post_type == "marketing-post" else "kb",
                url="https://example.com/evidence" if post_type == "marketing-post" else None,
                title="Reentrancy",
                evidence_text="External calls can reenter before state is finalized.",
            )
        ],
        kb_allowed=post_type != "marketing-post",
    )
    return validate(draft, pack, spec, rules)


def test_em_dash_is_rejected_without_normalization(rules):
    result = check("Reentrancy\u2014a classic bug.", rules)

    assert "ai_text:em_dash" in result.violations
    assert result.normalized.tweets[0].text == "Reentrancy\u2014a classic bug."
    assert "tweet_1:banned_characters" not in result.fixes


def test_curly_quotes_are_rejected_without_normalization(rules):
    result = check("\u201cState before calls.\u201d", rules)

    assert "ai_text:curly_quotes" in result.violations
    assert result.normalized.tweets[0].text == "\u201cState before calls.\u201d"
    assert "tweet_1:curly_quotes" not in result.fixes


def test_other_configured_dash_look_alikes_are_still_normalized(rules):
    result = check("Checks\u2012effects\u2011interactions order.", rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == "Checks-effects-interactions order."
    assert result.fixes == ["tweet_1:banned_characters"]


def test_protected_url_is_not_rewritten(rules):
    result = check("See https://example.com/non\u2011breaking", rules)

    assert result.normalized.tweets[0].text == "See https://example.com/non\u2011breaking"
    assert not result.accepted
    assert "tweet_1:banned_character:\u2011" in result.violations


@pytest.mark.parametrize("text", ["word \u2014 word", "word, \u2014 word", "word \u2014, word"])
def test_em_dash_is_rejected_at_punctuation_boundaries(rules, text):
    result = check(text, rules)

    assert "ai_text:em_dash" in result.violations
    assert result.normalized.tweets[0].text == text


@pytest.mark.parametrize(
    "text,violation",
    [
        ("https://example.com/a\u2014b", "ai_text:em_dash"),
        ("See https://example.com/non\u2011breaking", "tweet_1:banned_character:\u2011"),
    ],
)
def test_banned_characters_inside_urls_are_rejected_without_rewriting(rules, text, violation):
    result = check(text, rules)

    assert violation in result.violations
    assert result.normalized.tweets[0].text == text


@pytest.mark.parametrize("text", ["\u2014", "\u2013", "\u201c\u201d"])
def test_rejected_banned_characters_cannot_become_empty_content(rules, text):
    result = check(text, rules)

    assert not result.accepted
    assert result.normalized.tweets[0].text == text


def test_whitespace_and_markdown_fixes_remain_available(rules):
    result = check("  State   before *external* calls. #audit  ", rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == "State before external calls."
    assert result.fixes == [
        "tweet_1:whitespace",
        "tweet_1:trailing_hashtags",
        "tweet_1:markdown",
    ]
