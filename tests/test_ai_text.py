"""Tests for content_pipeline/ai_text.py.

These tests build all their input inline; no KB IDs are needed.
The tests are written to be RED when ai_text.py is missing and GREEN after it
is added and wired into validate().

Structure
---------
Part A: unit tests for check_ai_text() and sentence_length_stats() directly.
Part B: integration tests – ai_text violations reject drafts via validate().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content_pipeline.ai_text import check_ai_text, policy_digest, sentence_length_stats
from content_pipeline.config import load_brand, load_type
from content_pipeline.models import BrandRules, Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.stage_replay import ValidationInput
from content_pipeline.validation import validate

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _pack(slot_id: str = "s1") -> ResearchPack:
    return ResearchPack(
        slot_id=slot_id,
        topic_id="SS-03699",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="SS-03699",
                kind="kb",
                title="Reentrancy",
                evidence_text=(
                    "External calls can reenter before state is finalized. "
                    "The version v1.2 applies. Range 0.4-0.8 is valid."
                ),
            )
        ],
    )


def _rules() -> BrandRules:
    return load_brand(ROOT / "inputs/brand-voice.md")[0]


def _validate(text: str, post_type: str = "marketing-post") -> object:
    """Run validate() on a single-tweet draft with *text*."""
    spec = load_type(ROOT, post_type)
    draft = Draft(
        slot_id="s1",
        tweets=[Tweet(text=text, source_ids=["SS-03699"])]
        + [
            Tweet(text="Update state before external calls.", source_ids=["SS-03699"])
            for _ in range(spec.units.min - 1)
        ],
    )
    return validate(draft, _pack(), spec, _rules())


# ===========================================================================
# Part A: unit tests for check_ai_text()
# ===========================================================================


class TestEmDash:
    def test_em_dash_flagged(self):
        assert "ai_text:em_dash" in check_ai_text("word\u2014word")

    def test_clean_text_not_flagged(self):
        assert "ai_text:em_dash" not in check_ai_text("word-word")

    def test_en_dash_not_flagged_as_em(self):
        assert "ai_text:em_dash" not in check_ai_text("word\u2013word")


class TestEnDashSeparator:
    def test_spaced_en_dash_flagged(self):
        assert "ai_text:en_dash_separator" in check_ai_text("word \u2013 word")

    def test_ascii_hyphen_range_not_flagged(self):
        """0.4-0.8 must NOT trigger en_dash_separator."""
        assert "ai_text:en_dash_separator" not in check_ai_text("Range 0.4-0.8 is valid.")

    def test_pre_version_hyphen_not_flagged(self):
        """pre-0.6.0 must NOT trigger en_dash_separator."""
        assert "ai_text:en_dash_separator" not in check_ai_text("pre-0.6.0 applies.")

    def test_unspaced_en_dash_not_separator(self):
        """An en dash without spaces on both sides is not a separator."""
        assert "ai_text:en_dash_separator" not in check_ai_text("word\u2013word")

    def test_en_dash_at_line_start_flagged(self):
        """En dash at the very start of text (no preceding space) is still a separator."""
        assert "ai_text:en_dash_separator" in check_ai_text("\u2013 word")


class TestCurlyQuotes:
    @pytest.mark.parametrize("ch", ["\u2018", "\u2019", "\u201c", "\u201d"])
    def test_each_curly_quote_flagged(self, ch):
        assert "ai_text:curly_quotes" in check_ai_text(f"word {ch}quote{ch} word")

    def test_straight_quotes_not_flagged(self):
        assert "ai_text:curly_quotes" not in check_ai_text('word "quote" word')

    def test_straight_apostrophe_not_flagged(self):
        assert "ai_text:curly_quotes" not in check_ai_text("it's fine")


class TestEmoji:
    def test_emoji_flagged(self):
        # U+1F600 GRINNING FACE
        assert "ai_text:emoji" in check_ai_text("Great \U0001f600 work")

    def test_flag_emoji_flagged(self):
        # Regional indicator letters (flag emoji)
        assert "ai_text:emoji" in check_ai_text("\U0001f1fa\U0001f1f8 flag")

    def test_plain_ascii_not_flagged(self):
        assert "ai_text:emoji" not in check_ai_text("Great work :)")

    def test_regular_symbol_not_flagged(self):
        # A basic ASCII or standard punctuation character
        assert "ai_text:emoji" not in check_ai_text("result: 42%")


class TestBannedWords:
    @pytest.mark.parametrize(
        "word",
        [
            "delve",
            "leverage",
            "tapestry",
            "underscore",
            "foster",
            "pivotal",
            "realm",
            "landscape",
            "crucial",
            "seamless",
            "robust",
            "navigate",
            "testament",
            "ever-evolving",
        ],
    )
    def test_each_banned_word_flagged(self, word):
        violations = check_ai_text(f"We {word} this approach.")
        assert f"ai_text:banned_word:{word}" in violations

    def test_word_boundary_respected(self):
        """Whole-word match: 'leverage' as a standalone word is caught."""
        assert "ai_text:banned_word:leverage" in check_ai_text("We leverage that approach.")

    def test_word_inside_larger_word_not_flagged(self):
        """'leverage' should not be caught inside a word like 'leverages' via whole-word check.

        Note: the current implementation uses (?<!\\w)...(?!\\w) which means
        'leverages' ending in 's' — 'leverage' IS bounded because 's' is \\w.
        We instead verify the positive case still works.
        """
        # 'leverage' as a standalone word should be caught
        violations = check_ai_text("The leverages and understatement are unrelated terms.")
        assert "ai_text:banned_word:leverage" not in violations
        assert "ai_text:banned_word:underscore" not in violations

    def test_code_spans_and_urls_are_not_lexical_prose(self):
        violations = check_ai_text(
            "Use `leverage` as the local variable. https://example.com/robust/details"
        )
        assert "ai_text:banned_word:leverage" not in violations
        assert "ai_text:banned_word:robust" not in violations

    def test_unrelated_word_not_flagged(self):
        assert not any(
            v.startswith("ai_text:banned_word:")
            for v in check_ai_text("External calls reenter before state is updated.")
        )

    def test_case_insensitive(self):
        assert "ai_text:banned_word:leverage" in check_ai_text("We Leverage this.")


class TestNegativeParallelism:
    def test_not_just_but_flagged(self):
        assert "ai_text:negative_parallelism" in check_ai_text(
            "Not just speed, but also correctness."
        )

    def test_not_only_but_also_flagged(self):
        assert "ai_text:negative_parallelism" in check_ai_text(
            "Not only is it fast, but also secure."
        )

    def test_clean_sentence_not_flagged(self):
        assert "ai_text:negative_parallelism" not in check_ai_text(
            "Speed and correctness both matter."
        )


class TestCopulaAvoidance:
    @pytest.mark.parametrize("phrase", ["serves as", "stands as", "boasts"])
    def test_phrase_flagged(self, phrase):
        assert f"ai_text:copula_avoidance:{phrase}" in check_ai_text(
            f"This contract {phrase} a guard."
        )

    def test_clean_phrasing_not_flagged(self):
        assert not any(
            v.startswith("ai_text:copula_avoidance:")
            for v in check_ai_text("This contract checks the caller.")
        )


class TestFillerOpener:
    @pytest.mark.parametrize(
        "opener",
        [
            "In today's fast-paced world, security matters.",
            "It is important to note that reentrancy is dangerous.",
            "It's important to note that state must be updated first.",
        ],
    )
    def test_filler_opener_flagged(self, opener):
        assert "ai_text:filler_opener" in check_ai_text(opener)

    def test_normal_opener_not_flagged(self):
        assert "ai_text:filler_opener" not in check_ai_text(
            "External calls can reenter before state is updated."
        )

    def test_filler_opener_in_later_tweet_is_flagged(self):
        """Every tweet has its own opening, not only the first tweet."""
        text = "First tweet.\nIn today's fast-paced world, security matters."
        assert "ai_text:filler_opener" in check_ai_text(text)


class TestRuleOfThree:
    def test_two_three_item_lists_flagged(self):
        text = (
            "We check speed, accuracy, and correctness. "
            "We also verify latency, throughput, and reliability."
        )
        assert "ai_text:rule_of_three" in check_ai_text(text)

    def test_single_three_item_list_not_flagged(self):
        assert "ai_text:rule_of_three" not in check_ai_text(
            "We check speed, accuracy, and correctness."
        )

    def test_no_list_not_flagged(self):
        assert "ai_text:rule_of_three" not in check_ai_text(
            "External calls reenter before state is updated."
        )


class TestHedgingDensity:
    def test_high_hedging_flagged(self):
        # Many hedge words in a short text → density > 3.0
        text = "This may or may not work. It could generally typically succeed."
        violations = check_ai_text(text)
        assert any(v.startswith("ai_text:hedging_density:") for v in violations)

    def test_low_hedging_not_flagged(self):
        text = (
            "External calls reenter before state is updated. Always update state before the call."
        )
        assert not any(v.startswith("ai_text:hedging_density:") for v in check_ai_text(text))

    def test_violation_includes_actual_and_threshold(self):
        text = "This may or may not work. It could generally typically succeed."
        violations = check_ai_text(text)
        hedge_violations = [v for v in violations if v.startswith("ai_text:hedging_density:")]
        assert hedge_violations
        # Format: ai_text:hedging_density:<actual>><threshold>
        payload = hedge_violations[0].split(":", 2)[2]
        assert ">" in payload

    def test_mid_sentence_month_may_is_not_a_hedge(self):
        violations = check_ai_text("The patch shipped in May after the audit.")
        assert not any(v.startswith("ai_text:hedging_density:") for v in violations)


# ===========================================================================
# Part A: sentence_length_stats()
# ===========================================================================


class TestSentenceLengthStats:
    def test_basic_stats(self):
        text = "External calls reenter. Always update state before the call. Check every path."
        stats = sentence_length_stats(text)
        assert stats["count"] == 3
        assert stats["mean"] > 0
        assert stats["stdev"] >= 0

    def test_empty_text(self):
        stats = sentence_length_stats("")
        assert stats == {"count": 0, "mean": 0.0, "stdev": 0.0}

    def test_single_sentence(self):
        stats = sentence_length_stats("External calls reenter before state is updated.")
        assert stats["count"] == 1
        assert stats["stdev"] == 0.0

    def test_returns_dict_with_required_keys(self):
        stats = sentence_length_stats("Hello world.")
        assert set(stats.keys()) == {"count", "mean", "stdev"}

    def test_newlines_are_sentence_boundaries(self):
        stats = sentence_length_stats("One short tweet\nA second longer tweet")
        assert stats["count"] == 2


class TestEmojiPresentation:
    @pytest.mark.parametrize("symbol", ["™", "✓", "⌘", "⌨"])
    def test_text_symbols_are_not_emoji(self, symbol):
        assert "ai_text:emoji" not in check_ai_text(f"Use {symbol} in technical prose.")

    @pytest.mark.parametrize("emoji", ["🚀", "✅"])
    def test_emoji_presentation_is_rejected(self, emoji):
        assert "ai_text:emoji" in check_ai_text(f"Ship {emoji} now.")


class TestSnapshotPolicy:
    def test_policy_root_controls_validation_and_replay_identity(self, tmp_path):
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        vocabulary = inputs / "ai-vocabulary.txt"
        constructions = inputs / "ai-constructions.yaml"
        vocabulary.write_text("snapshot-only\n", encoding="utf-8")
        constructions.write_text(
            (ROOT / "inputs/ai-constructions.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )

        result = validate(
            Draft(
                slot_id="s1", tweets=[Tweet(text="snapshot-only wording", source_ids=["SS-03699"])]
            ),
            _pack(),
            load_type(ROOT, "marketing-post"),
            _rules(),
            ai_policy_root=tmp_path,
        )

        assert "ai_text:banned_word:snapshot-only" in result.violations
        digest = policy_digest(tmp_path)
        assert digest is not None
        replay_input = ValidationInput(
            draft=result.original,
            research=_pack(),
            post_type=load_type(ROOT, "marketing-post"),
            max_tweet_chars=280,
            ai_policy_sha256=digest,
        )
        assert replay_input.ai_policy_sha256 == digest


# ===========================================================================
# Part B: integration – ai_text violations reject drafts via validate()
# ===========================================================================


class TestValidateIntegration:
    def test_em_dash_in_draft_rejected(self):
        """An em dash in the draft text is rejected with ai_text:em_dash."""
        result = _validate("Reentrancy\u2014a classic bug\u2014lets attackers reenter.")
        assert not result.accepted
        assert "ai_text:em_dash" in result.violations

    def test_curly_quotes_in_draft_rejected(self):
        """Curly quotes in the draft text are rejected with ai_text:curly_quotes."""
        result = _validate("\u201cState before calls.\u201d")
        assert not result.accepted
        assert "ai_text:curly_quotes" in result.violations

    def test_en_dash_separator_in_draft_rejected(self):
        """An en dash used as separator is rejected with ai_text:en_dash_separator."""
        result = _validate("Reentrancy \u2013 a critical bug \u2013 exploits state.")
        assert not result.accepted
        assert "ai_text:en_dash_separator" in result.violations

    def test_ascii_hyphen_range_not_rejected(self):
        """ASCII hyphens in version ranges must NOT trigger ai_text:en_dash_separator."""
        result = _validate("The range 0.4-0.8 is valid for version v1.2.")
        # Should not have en_dash_separator violation
        assert "ai_text:en_dash_separator" not in result.violations

    def test_banned_word_leverage_rejected(self):
        """The word 'leverage' in a draft is rejected with ai_text:banned_word:leverage."""
        result = _validate("We leverage this pattern in smart contracts.")
        assert not result.accepted
        assert "ai_text:banned_word:leverage" in result.violations

    def test_clean_draft_no_ai_text_violations(self):
        """A clean draft has no ai_text violations."""
        result = _validate("External calls reenter before state is updated.")
        ai_violations = [v for v in result.violations if v.startswith("ai_text:")]
        assert ai_violations == []

    def test_emoji_in_draft_rejected(self):
        """Emoji in a draft are rejected with ai_text:emoji."""
        result = _validate("Great security \U0001f600 always matters.")
        assert not result.accepted
        assert "ai_text:emoji" in result.violations
