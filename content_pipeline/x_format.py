"""How text looks on X: tweet numbering, sentence splits for length, and markdown X would show
literally. This is formatting for one platform, kept apart from the rule checks in
validation.py. Nothing here adds or changes a word of the writer's content."""

import re
from twitter_text import parse_tweet
from .models import Draft, MarketingFormat, PostType, Tweet


_BARE_DOMAIN_URL = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/\S*")
# A sentence ends at ., ? or ! and the next sentence starts with a capital, backtick or digit.
_SENTENCE_BREAK = re.compile(r"(?<=[.?!])\s+(?=[A-Z`0-9])")
NUMBERING_PREFIX = re.compile(r"^\d+/\d+\s+")


def is_url_token(token: str) -> bool:
    """Recognize URL tokens whose banned characters must stay unchanged."""
    return (
        "://" in token or token.startswith("www.") or _BARE_DOMAIN_URL.fullmatch(token) is not None
    )


def numbering(
    spec: PostType, position: int, count: int, format: MarketingFormat | None = None
) -> str:
    """The prefix added to a tweet of a numbered type, such as "3/7 "."""
    return f"{position}/{count} " if (format.numbering if format else spec.numbering) else ""


def _inside_code_span(text: str, position: int) -> bool:
    return text[:position].count("`") % 2 == 1


def _ends_with_url(text: str, position: int) -> bool:
    """Whether the whitespace-delimited token that ends at position is a URL."""
    return is_url_token(text[:position].split()[-1])


def split_at_sentence(text: str, limit: int, prefix: str = "") -> tuple[str, str] | None:
    """Split at the sentence boundary that best balances the halves, if both fit the limit."""
    best = None
    best_gap = None

    for match in _SENTENCE_BREAK.finditer(text):
        if _inside_code_span(text, match.start()) or _ends_with_url(text, match.start()):
            continue

        first = text[: match.start()]
        second = text[match.end() :]

        first_length = parse_tweet(prefix + first).weightedLength
        second_length = parse_tweet(prefix + second).weightedLength

        if max(first_length, second_length) > limit:
            continue

        gap = abs(first_length - second_length)

        if best_gap is None or gap < best_gap:
            best = (first, second)
            best_gap = gap

    return best


def split_overlong_tweets(
    draft: Draft,
    spec: PostType,
    limit: int,
    fixes: list[str],
    format: MarketingFormat | None = None,
) -> None:
    """Split tweets over the limit in place while the thread is below the type's unit maximum."""
    units = format.units if format else spec.units

    if units.max < 2:
        return

    index = 0

    while index < len(draft.tweets) and len(draft.tweets) < units.max:
        count = len(draft.tweets)
        tweet = draft.tweets[index]

        numbered = numbering(spec, index + 1, count, format) + tweet.text

        if parse_tweet(numbered).weightedLength <= limit:
            index += 1

            continue

        # Every later tweet is renumbered, so the halves must fit with the widest prefix.
        widest = numbering(spec, count + 1, count + 1, format)

        # Going from 9 to 10 tweets widens every prefix ("1/9 " to "1/10 "); refuse a split
        # that would push a tweet that already fits over the limit.
        if len(widest) > len(numbering(spec, count, count, format)) and any(
            parse_tweet(widest + other.text).weightedLength > limit
            for position, other in enumerate(draft.tweets)
            if position != index
        ):
            index += 1

            continue

        text = NUMBERING_PREFIX.sub("", tweet.text) if numbering(spec, 1, 1, format) else tweet.text
        halves = split_at_sentence(text, limit, widest)

        if halves is None:
            index += 1

            continue

        draft.tweets[index : index + 1] = [
            Tweet(text=half, source_ids=list(tweet.source_ids)) for half in halves
        ]

        fixes.append(f"tweet_{index + 1}:split_for_length")


# A paired backtick code span. An unpaired backtick is not a span.
CODE_SPAN = re.compile(r"`[^`]*`")
# Paired * or ** around a word or phrase. X shows the asterisks literally, so the words stay and
# the markers go. Code spans are skipped, so `a * b` keeps its operator.
_EMPHASIS = re.compile(r"(?<![\w*])(\*{1,2})(?=\S)([^*`]+?)(?<=\S)\1(?![\w*])")
# A fenced block becomes one inline code span. Only a known language tag followed by space and
# then code (not an operator) is dropped, so "```require(x);```" and "```text = f()```" keep
# their first word. Newlines are already flattened by the whitespace fix, so none is required.
_FENCE = re.compile(
    r"```(?:(?:solidity|sol|javascript|js|typescript|ts|python|py|bash|sh|json|yaml|text)"
    r"(?=\s)(?!\s*[=(.\[:]))?\s*(.+?)\s*```",
    re.S | re.I,
)


def strip_markdown(text: str) -> str:
    """Turn code fences into inline code and remove emphasis markers outside code spans."""
    parts = _FENCE.sub(r"`\1`", text).split("`")

    return "`".join(
        part if index % 2 else _EMPHASIS.sub(r"\2", part) for index, part in enumerate(parts)
    )
