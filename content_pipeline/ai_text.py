"""Table-driven AI-text quality checks for the content pipeline.

Checks run on the full piece text (all tweets joined by newlines) BEFORE
safe-fixes remove em dashes and curly quotes, so they catch the raw writer
output.  Each check returns a violation string such as ``ai_text:em_dash``.

Public API
----------
check_ai_text(text) -> list[str]
    Return all AI-text violation codes found in *text*.

sentence_length_stats(text) -> dict
    Return {"count": int, "mean": float, "stdev": float} for logging.
"""

from __future__ import annotations

import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_INPUTS = Path(__file__).resolve().parents[1] / "inputs"
_VOCAB_FILE = _INPUTS / "ai-vocabulary.txt"
_CONSTRUCTIONS_FILE = _INPUTS / "ai-constructions.yaml"


# ---------------------------------------------------------------------------
# Lazy-loaded data (loaded once per interpreter session)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_vocab() -> tuple[str, ...]:
    """Banned AI vocabulary words/phrases, lower-cased, comment lines stripped."""
    lines = _VOCAB_FILE.read_text(encoding="utf-8").splitlines()
    return tuple(
        line.strip().casefold()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    )


@lru_cache(maxsize=1)
def _load_constructions() -> dict:
    """Raw YAML payload from ai-constructions.yaml."""
    return yaml.safe_load(_CONSTRUCTIONS_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Character-level helpers
# ---------------------------------------------------------------------------

_EM_DASH = "\u2014"
_EN_DASH = "\u2013"
_CURLY_OPEN_SINGLE = "\u2018"
_CURLY_CLOSE_SINGLE = "\u2019"
_CURLY_OPEN_DOUBLE = "\u201c"
_CURLY_CLOSE_DOUBLE = "\u201d"

# En dash used as a sentence separator: surrounded by spaces (word – word).
# We do NOT flag ASCII hyphen '-' or plain ranges like "0.4-0.8".
_EN_DASH_SEPARATOR = re.compile(r"(?:^|\s)\u2013(?=\s|$)")


def _has_em_dash(text: str) -> bool:
    return _EM_DASH in text


def _has_curly_quotes(text: str) -> bool:
    return any(
        ch in text
        for ch in (_CURLY_OPEN_SINGLE, _CURLY_CLOSE_SINGLE, _CURLY_OPEN_DOUBLE, _CURLY_CLOSE_DOUBLE)
    )


def _has_en_dash_separator(text: str) -> bool:
    """True when U+2013 appears as a sentence separator (space – space)."""
    return bool(_EN_DASH_SEPARATOR.search(text))


# ---------------------------------------------------------------------------
# Emoji detection
# ---------------------------------------------------------------------------


def _is_emoji(ch: str) -> bool:
    """Return True when *ch* is an emoji or emoji-presentation character."""
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF  # Misc Symbols, Pictographs, Emoticons, Supplemental Symbols
        or 0x2600 <= cp <= 0x27BF  # Misc Symbols, Dingbats
        or 0x1F000 <= cp <= 0x1F02F  # Mahjong tiles (block used for emoji)
        or 0xFE00 <= cp <= 0xFE0F  # Variation selectors (emoji presentation)
        or cp == 0x200D  # ZWJ (used in emoji sequences)
        or 0x1F1E0 <= cp <= 0x1F1FF  # Regional indicator symbols (flags)
        or 0x2300 <= cp <= 0x23FF  # Miscellaneous Technical (⏰ etc.)
        or 0x2B50 <= cp <= 0x2B55  # Stars / circles
        or (unicodedata.category(ch) == "So" and cp > 0x2000)
    )


def _has_emoji(text: str) -> bool:
    return any(_is_emoji(ch) for ch in text)


# ---------------------------------------------------------------------------
# Banned vocabulary check
# ---------------------------------------------------------------------------


def _banned_word_violations(text: str) -> list[str]:
    """Return ai_text:banned_word:<word> for each vocab entry found in *text*."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    violations = []
    for phrase in _load_vocab():
        if " " in phrase or "-" in phrase:
            # Multi-word or hyphenated phrase: plain substring search
            if phrase in folded:
                violations.append(f"ai_text:banned_word:{phrase}")
        else:
            # Single word: require word boundaries
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
            if re.search(pattern, folded):
                violations.append(f"ai_text:banned_word:{phrase}")
    return violations


# ---------------------------------------------------------------------------
# Construction pattern checks (loaded from YAML)
# ---------------------------------------------------------------------------


def _negative_parallelism_violation(text: str) -> list[str]:
    cfg = _load_constructions()
    folded = unicodedata.normalize("NFKC", text).casefold()
    for pattern in cfg.get("negative_parallelism", {}).get("patterns", []):
        if re.search(pattern, folded):
            return ["ai_text:negative_parallelism"]
    return []


def _copula_avoidance_violations(text: str) -> list[str]:
    cfg = _load_constructions()
    folded = unicodedata.normalize("NFKC", text).casefold()
    violations = []
    for phrase in cfg.get("copula_avoidance", {}).get("phrases", []):
        if phrase in folded:
            violations.append(f"ai_text:copula_avoidance:{phrase}")
    return violations


# Numbering prefix pattern, matching "1/5 " or "3/8 " etc. (same as x_format)
_NUMBERING_PREFIX = re.compile(r"^\d+/\d+\s+")


def _filler_opener_violation(text: str) -> list[str]:
    """Check the first non-empty line/tweet for filler opener patterns."""
    cfg = _load_constructions()
    for line in text.splitlines():
        stripped = _NUMBERING_PREFIX.sub("", line.strip())
        if not stripped:
            continue
        folded = unicodedata.normalize("NFKC", stripped).casefold()
        for pattern in cfg.get("filler_openers", {}).get("patterns", []):
            if folded.startswith(pattern.casefold()):
                return ["ai_text:filler_opener"]
        break  # Only check the first non-empty line
    return []


# ---------------------------------------------------------------------------
# Rule of three: flag when 3-item "A, B, and C" list pattern appears 2+ times
# ---------------------------------------------------------------------------


def _rule_of_three_violation(text: str) -> list[str]:
    cfg = _load_constructions()
    folded = unicodedata.normalize("NFKC", text).casefold()
    rule = cfg.get("rule_of_three", {})
    pattern = rule.get("pattern")
    minimum_matches = int(rule.get("minimum_matches", 2))
    if pattern and len(re.findall(pattern, folded)) >= minimum_matches:
        return ["ai_text:rule_of_three"]
    return []


# ---------------------------------------------------------------------------
# Hedging density
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\b[a-z]+\b")


def _hedging_density_violation(text: str) -> list[str]:
    cfg = _load_constructions()
    hedge_words: list[str] = cfg.get("hedge_words", [])
    threshold: float = float(cfg.get("hedging_threshold", 3.0))

    folded = unicodedata.normalize("NFKC", text).casefold()
    all_words = _WORD_RE.findall(folded)
    total = len(all_words)

    if total == 0:
        return []

    hedge_set = frozenset(w.casefold() for w in hedge_words)
    hedge_count = sum(1 for w in all_words if w in hedge_set)
    density = hedge_count / total * 100

    if density > threshold:
        return [f"ai_text:hedging_density:{density:.1f}>{threshold}"]
    return []


# ---------------------------------------------------------------------------
# Sentence length stats
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sentence_length_stats(text: str) -> dict:
    """Return sentence-length statistics: count, mean, stdev (word counts).

    Returns {"count": 0, "mean": 0.0, "stdev": 0.0} when there are no sentences.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        return {"count": 0, "mean": 0.0, "stdev": 0.0}

    lengths = [len(s.split()) for s in sentences]
    count = len(lengths)
    mean = sum(lengths) / count

    if count < 2:
        stdev = 0.0
    else:
        variance = sum((length - mean) ** 2 for length in lengths) / (count - 1)
        stdev = math.sqrt(variance)

    return {"count": count, "mean": round(mean, 2), "stdev": round(stdev, 2)}


# ---------------------------------------------------------------------------
# Main check entry point
# ---------------------------------------------------------------------------


def check_ai_text(text: str) -> list[str]:
    """Return all AI-text violation codes found in *text*.

    Runs on the original (pre-safe-fix) piece text so em dashes, curly quotes
    and en dashes have not yet been stripped.  The caller (validate()) runs
    this on the original tweet texts joined by newlines.

    Violation codes (no tweet prefix; piece-level):
        ai_text:em_dash
        ai_text:en_dash_separator
        ai_text:curly_quotes
        ai_text:emoji
        ai_text:banned_word:<word>
        ai_text:negative_parallelism
        ai_text:copula_avoidance:<phrase>
        ai_text:filler_opener
        ai_text:rule_of_three
        ai_text:hedging_density:<actual>><threshold>
    """
    violations: list[str] = []

    # Character-level checks (run on original text, before any safe fixes)
    if _has_em_dash(text):
        violations.append("ai_text:em_dash")

    if _has_en_dash_separator(text):
        violations.append("ai_text:en_dash_separator")

    if _has_curly_quotes(text):
        violations.append("ai_text:curly_quotes")

    if _has_emoji(text):
        violations.append("ai_text:emoji")

    # Vocabulary and construction checks
    violations.extend(_banned_word_violations(text))
    violations.extend(_negative_parallelism_violation(text))
    violations.extend(_copula_avoidance_violations(text))
    violations.extend(_filler_opener_violation(text))
    violations.extend(_rule_of_three_violation(text))
    violations.extend(_hedging_density_violation(text))

    return violations
