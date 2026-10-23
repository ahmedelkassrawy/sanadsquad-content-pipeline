"""Table-driven AI-text quality checks."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

_DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_EM_DASH = "\u2014"
_CURLY_QUOTES = "\u2018\u2019\u201c\u201d"
_EN_DASH_SEPARATOR = re.compile(r"(?:^|\s)\u2013(?=\s|$)")
_NUMBERING_PREFIX = re.compile(r"^\d+/\d+\s+")
_WORD_RE = re.compile(r"\b[a-z]+\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CODE_OR_URL = re.compile(r"`[^`]*`|https?://\S+")


def _policy_paths(root: Path | None) -> tuple[Path, Path]:
    inputs = (root or _DEFAULT_ROOT) / "inputs"
    return inputs / "ai-vocabulary.txt", inputs / "ai-constructions.yaml"


def has_policy_files(root: Path | None) -> bool:
    return all(path.is_file() for path in _policy_paths(root))


def policy_digest(root: Path | None) -> str | None:
    """Digest the policy that affects validation replay; None preserves legacy runs."""
    vocabulary, constructions = _policy_paths(root)
    if not vocabulary.is_file() or not constructions.is_file():
        return None
    return hashlib.sha256(vocabulary.read_bytes() + constructions.read_bytes()).hexdigest()


def _policy_root(root: Path | None) -> Path:
    # Old snapshots predate the policy files and remain readable using the installed default.
    return root if has_policy_files(root) else _DEFAULT_ROOT


@lru_cache(maxsize=None)
def _load_vocab(root: Path) -> tuple[str, ...]:
    vocabulary, _ = _policy_paths(root)
    return tuple(
        line.strip().casefold()
        for line in vocabulary.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


@lru_cache(maxsize=None)
def _load_constructions(root: Path) -> dict:
    _, constructions = _policy_paths(root)
    return yaml.safe_load(constructions.read_text(encoding="utf-8"))


# Emoji_Presentation-style ranges. Avoid whole symbol blocks and all So characters:
# ™, ✓, ⌘ and ⌨ pass; ✅ and pictographic emoji reject.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x1F1E0, 0x1F1FF),
    (0x231A, 0x231B),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x25AA, 0x25AB),
    (0x25FB, 0x25FE),
    (0x2600, 0x2604),
    (0x2614, 0x2615),
    (0x2622, 0x2623),
    (0x262E, 0x262F),
    (0x2638, 0x263A),
    (0x2648, 0x2653),
    (0x265F, 0x2660),
    (0x2665, 0x2666),
    (0x267E, 0x267F),
    (0x2692, 0x2697),
    (0x269B, 0x269C),
    (0x26A0, 0x26A1),
    (0x26AA, 0x26AB),
    (0x26B0, 0x26B1),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26CE, 0x26CF),
    (0x26D3, 0x26D4),
    (0x26E9, 0x26EA),
    (0x26F0, 0x26F5),
    (0x26F7, 0x26FA),
    (0x2708, 0x270D),
    (0x2733, 0x2734),
    (0x2753, 0x2755),
    (0x2763, 0x2764),
    (0x2795, 0x2797),
    (0x2B05, 0x2B07),
    (0x2B1B, 0x2B1C),
)
_EMOJI_POINTS = frozenset(
    (
        0x25B6,
        0x25C0,
        0x260E,
        0x2611,
        0x2618,
        0x261D,
        0x2620,
        0x2626,
        0x262A,
        0x2640,
        0x2642,
        0x2663,
        0x2668,
        0x267B,
        0x2699,
        0x26A7,
        0x26C8,
        0x26D1,
        0x26FD,
        0x2702,
        0x2705,
        0x270F,
        0x2712,
        0x2714,
        0x2716,
        0x271D,
        0x2721,
        0x2728,
        0x2744,
        0x2747,
        0x274C,
        0x274E,
        0x2757,
        0x27A1,
        0x27B0,
        0x27BF,
        0x2934,
        0x2935,
        0x2B50,
        0x2B55,
        0x3030,
        0x303D,
        0x3297,
        0x3299,
    )
)


def _has_emoji(text: str) -> bool:
    return any(
        ord(ch) in _EMOJI_POINTS or any(start <= ord(ch) <= end for start, end in _EMOJI_RANGES)
        for ch in text
    )


def _mask_code_and_urls(text: str) -> str:
    """Keep boundaries/newlines while removing non-prose lexical material."""
    return _CODE_OR_URL.sub(lambda match: re.sub(r"\S", " ", match.group()), text)


def _banned_word_violations(text: str, root: Path) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    violations = []
    for phrase in _load_vocab(root):
        if " " in phrase or "-" in phrase:
            if phrase in folded:
                violations.append(f"ai_text:banned_word:{phrase}")
        elif re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", folded):
            violations.append(f"ai_text:banned_word:{phrase}")
    return violations


def _negative_parallelism_violation(text: str, root: Path) -> list[str]:
    patterns = _load_constructions(root).get("negative_parallelism", {}).get("patterns", [])
    return (
        ["ai_text:negative_parallelism"]
        if any(re.search(p, text.casefold()) for p in patterns)
        else []
    )


def _copula_avoidance_violations(text: str, root: Path) -> list[str]:
    phrases = _load_constructions(root).get("copula_avoidance", {}).get("phrases", [])
    return [f"ai_text:copula_avoidance:{phrase}" for phrase in phrases if phrase in text.casefold()]


def _filler_opener_violation(text: str, root: Path) -> list[str]:
    patterns = _load_constructions(root).get("filler_openers", {}).get("patterns", [])
    for line in text.splitlines():
        opening = _NUMBERING_PREFIX.sub("", line.strip()).casefold()
        if opening and any(opening.startswith(pattern.casefold()) for pattern in patterns):
            return ["ai_text:filler_opener"]
    return []


def _rule_of_three_violation(text: str, root: Path) -> list[str]:
    rule = _load_constructions(root).get("rule_of_three", {})
    pattern = rule.get("pattern")
    return (
        ["ai_text:rule_of_three"]
        if pattern
        and len(re.findall(pattern, text.casefold())) >= int(rule.get("minimum_matches", 2))
        else []
    )


def _hedging_density_violation(text: str, root: Path) -> list[str]:
    cfg = _load_constructions(root)
    threshold = float(cfg.get("hedging_threshold", 3.0))
    normalized = unicodedata.normalize("NFKC", text)
    words = list(_WORD_RE.finditer(normalized.casefold()))
    if not words:
        return []
    hedge_words = frozenset(word.casefold() for word in cfg.get("hedge_words", []))
    count = 0
    for word in words:
        month = (
            normalized[word.start() : word.end()] == "May"
            and bool(normalized[: word.start()].rstrip())
            and not normalized[: word.start()].rstrip().endswith((".", "!", "?"))
        )
        if word.group() in hedge_words and not month:
            count += 1
    density = count / len(words) * 100
    return [f"ai_text:hedging_density:{density:.1f}>{threshold}"] if density > threshold else []


def sentence_length_stats(text: str) -> dict:
    """Return sentence word-count statistics, treating each tweet newline as a boundary."""
    sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT.split(text) if sentence.strip()]
    if not sentences:
        return {"count": 0, "mean": 0.0, "stdev": 0.0}
    lengths = [len(sentence.split()) for sentence in sentences]
    mean = sum(lengths) / len(lengths)
    variance = (
        sum((length - mean) ** 2 for length in lengths) / (len(lengths) - 1)
        if len(lengths) > 1
        else 0.0
    )
    return {"count": len(lengths), "mean": round(mean, 2), "stdev": round(math.sqrt(variance), 2)}


def check_ai_text(text: str, policy_root: Path | None = None) -> list[str]:
    """Return configured AI-text violations for a piece of tweet text."""
    violations = []
    if _EM_DASH in text:
        violations.append("ai_text:em_dash")
    if _EN_DASH_SEPARATOR.search(text):
        violations.append("ai_text:en_dash_separator")
    if any(ch in text for ch in _CURLY_QUOTES):
        violations.append("ai_text:curly_quotes")
    if _has_emoji(text):
        violations.append("ai_text:emoji")
    root = _policy_root(policy_root)
    prose = _mask_code_and_urls(text)
    violations.extend(_banned_word_violations(prose, root))
    violations.extend(_negative_parallelism_violation(prose, root))
    violations.extend(_copula_avoidance_violations(prose, root))
    violations.extend(_filler_opener_violation(prose, root))
    violations.extend(_rule_of_three_violation(prose, root))
    violations.extend(_hedging_density_violation(prose, root))
    return violations
