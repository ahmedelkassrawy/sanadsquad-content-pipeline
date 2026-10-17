"""Validator rewrites configured banned characters (such as em dashes) before numbering."""

from pathlib import Path

import pytest

from content_pipeline.config import load_brand, load_type
from content_pipeline.models import BrandRules, Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.validation import validate


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rules():
    return load_brand(ROOT / "inputs/brand-voice.md")[0]


def check(text, rules: BrandRules, post_type="marketing-post"):
    spec = load_type(ROOT, post_type)
    draft = Draft(
        slot_id="s9",
        tweets=[Tweet(text=text, source_ids=["KB-01"])]
        + [
            Tweet(text="Update state before external calls.", source_ids=["KB-01"])
            for _ in range(spec.units.min - 1)
        ],
    )
    pack = ResearchPack(
        slot_id="s9",
        topic_id="KB-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-01",
                kind="kb",
                title="Reentrancy",
                evidence_text="External calls can reenter before state is finalized.",
            )
        ],
    )
    return validate(draft, pack, spec, rules)


def test_em_dashes_are_fixed_before_numbering_and_accepted(rules):
    text = "Reentrancy\u2014a classic bug\u2014lets attackers reenter."
    result = check(text, rules, "tech-thread")

    assert result.accepted, result.violations
    assert result.violations == []
    assert result.normalized.tweets[0].text == (
        "1/5 Reentrancy, a classic bug, lets attackers reenter."
    )
    assert result.fixes == ["tweet_1:banned_characters"]
    assert result.original.tweets[0].text == text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("word \u2014 word", "word, word"),
        ("word\t\n\u2014\t\nword", "word, word"),
        ("non\u2011breaking", "non-breaking"),
    ],
)
def test_configured_punctuation_is_rewritten(rules, text, expected):
    result = check(text, rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == expected
    assert result.fixes == ["tweet_1:banned_characters"]


def test_overlength_marketing_draft_still_fails_after_fix(rules):
    result = check("x" * 279 + "\u2014y", rules)

    assert not result.accepted
    assert "tweet_1:length" in result.violations
    assert not any("banned_character:" in error for error in result.violations)
    assert result.normalized.tweets[0].text == "x" * 279 + ", y"
    assert result.fixes == ["tweet_1:banned_characters"]


def test_em_dash_is_rejected_when_fix_is_disabled(rules):
    rules = rules.model_copy(
        update={
            "safe_fixes": [fix for fix in rules.safe_fixes if fix != "banned_characters"],
        }
    )
    result = check("word\u2014word", rules)

    assert not result.accepted
    assert result.violations == ["tweet_1:banned_character:\u2014"]
    assert result.normalized.tweets[0].text == "word\u2014word"
    assert result.fixes == []


def test_clean_text_records_no_banned_character_fix(rules):
    result = check("Update state before external calls.", rules)

    assert result.accepted, result.violations
    assert result.fixes == []


def test_banned_character_fix_runs_first_even_when_configured_last(rules):
    rules = rules.model_copy(
        update={
            "safe_fixes": [fix for fix in rules.safe_fixes if fix != "banned_characters"]
            + ["banned_characters"],
        }
    )
    result = check("word\u2014", rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == "word"
    assert result.fixes == ["tweet_1:banned_characters"]


def test_other_configured_banned_characters_are_removed(rules):
    rules = rules.model_copy(update={"banned_characters": [*rules.banned_characters, "~"]})
    result = check("word~ word", rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == "word word"
    assert result.fixes == ["tweet_1:banned_characters"]


def test_unconfigured_characters_are_preserved(rules):
    rules = rules.model_copy(update={"banned_characters": ["~"]})
    result = check("word\u2014non\u2011breaking", rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == "word\u2014non\u2011breaking"
    assert result.fixes == []


@pytest.mark.parametrize(
    "text,post_type,expected,accepted",
    [
        ("\u2014word", "marketing-post", "word", True),
        ("word\u2014", "marketing-post", "word", True),
        ("word\u2014\u2014word", "marketing-post", "word, word", True),
        ("word, \u2014 word", "marketing-post", "word, word", True),
        ("word\u2014, word", "marketing-post", "word, word", True),
        ("word\u2014.", "marketing-post", "word.", True),
        ("\u2014word", "tech-thread", "1/5 word", True),
        ("\u2014", "marketing-post", "", False),
        ("\u2011", "marketing-post", "-", False),
        (" \t\u2014\u2014 word \u2014\u2014 ", "marketing-post", "word", True),
        ("word \u2014 \u2014 word", "marketing-post", "word, word", True),
        ("\u2014", "tech-thread", "1/5 ", False),
        ("\u2011", "tech-thread", "1/5 -", False),
        ("...", "marketing-post", "...", False),
    ],
)
def test_banned_character_boundaries_and_empty_content(rules, text, post_type, expected, accepted):
    result = check(text, rules, post_type)

    assert result.normalized.tweets[0].text == expected
    assert result.accepted is accepted, result.violations
    assert result.violations == ([] if accepted else ["tweet_1:invalid_text"])
    assert result.original.tweets[0].text == text


@pytest.mark.parametrize("punctuation", list(",.;:!?)(\"'\u2018\u2019\u201c\u201d"))
@pytest.mark.parametrize("side", ["left", "right"])
def test_em_dash_next_to_punctuation(rules, punctuation, side):
    if side == "left":
        text = f"word{punctuation} \u2014 word"
        expected = f"word{punctuation}word" if punctuation == "(" else f"word{punctuation} word"
    else:
        text = f"word \u2014 {punctuation}word"
        expected = f"word {punctuation}word"
        if punctuation in ",.;:!?)":
            expected = f"word{punctuation}word"
    expected = expected.translate(
        str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
    )
    result = check(text, rules)

    assert result.accepted, result.violations
    assert result.normalized.tweets[0].text == expected


@pytest.mark.parametrize(
    "text,char",
    [
        ("See https://example.com/non\u2011breaking", "\u2011"),
        ("https://example.com/a\u2014b", "\u2014"),
        ("www.example.com/non\u2011breaking", "\u2011"),
        ("example.com/a\u2014b", "\u2014"),
        ("prefix:https://example.com/a\u2014b", "\u2014"),
    ],
)
def test_url_like_tokens_are_preserved_and_rejected(rules, text, char):
    result = check(text, rules)

    assert result.normalized.tweets[0].text == text
    assert not result.accepted
    assert f"tweet_1:banned_character:{char}" in result.violations
    assert result.fixes == []


def test_protected_url_does_not_prevent_fixing_surrounding_text(rules):
    result = check("word\u2014word https://example.com/non\u2011breaking", rules)

    assert result.normalized.tweets[0].text == "word, word https://example.com/non\u2011breaking"
    assert not result.accepted
    assert result.violations == ["tweet_1:banned_character:\u2011"]
    assert result.fixes == ["tweet_1:banned_characters"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ('He said "word\u2014".', 'He said "word".'),
        ("He said \u201cword\u2014\u201d.", 'He said "word".'),
        ('He said "word\u2014\u2014".', 'He said "word".'),
        ("[word\u2014]", "[word]"),
        ("{word\u2014}", "{word}"),
        ('word\u2014 "quote"', 'word "quote"'),
        ('word \u2014"quote"', 'word "quote"'),
        ("word\u2014 \u201cquote\u201d", 'word "quote"'),
    ],
)
def test_dash_run_respects_closing_and_opening_punctuation(rules, text, expected):
    result = check(text, rules)

    assert result.normalized.tweets[0].text == expected
    assert result.accepted, result.violations
    assert result.fixes[0] == "tweet_1:banned_characters"
    assert result.original.tweets[0].text == text


def test_version_token_is_not_protected_as_a_url(rules):
    result = check("v1.2/3\u2014word", rules)

    assert result.normalized.tweets[0].text == "v1.2/3, word"
    assert result.accepted, result.violations
    assert result.fixes == ["tweet_1:banned_characters"]
