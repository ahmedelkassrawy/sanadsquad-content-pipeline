import re
import unicodedata
from functools import partial
from urllib.parse import urlparse
from twitter_text import parse_tweet, extract_urls_with_indices
from .models import BrandRules, Draft, PostType, ResearchPack, ValidationResult


_QUOTES = "\"'\u201c\u201d\u2018\u2019"
_OPENING_BRACKETS = "([{"
_CLOSING_PUNCTUATION = ",.;:!?)]}"
_PUNCTUATION = _CLOSING_PUNCTUATION + _OPENING_BRACKETS + _QUOTES
_DASH_RUN = re.compile(r"\s*\u2014(?:\s*\u2014)*\s*")
_BARE_DOMAIN_URL = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/\S*")


def _is_url_token(token: str) -> bool:
    """Recognize URL tokens whose banned characters must stay unchanged."""
    return (
        "://" in token or token.startswith("www.") or _BARE_DOMAIN_URL.fullmatch(token) is not None
    )


def _replace_dash_run(left: str, right: str, dash_run: str) -> str:
    """Choose a separator from the dash run's surrounding text and spacing."""
    if not left or not right:
        return ""
    if right[0] in _QUOTES and dash_run.startswith("\u2014"):
        if dash_run.endswith("\u2014") and not left[-1].isspace():
            return ""
    if left[-1] in _PUNCTUATION or right[0] in _PUNCTUATION:
        if left[-1] in _OPENING_BRACKETS or right[0] in _CLOSING_PUNCTUATION:
            return ""
        return " "
    return ", "


def _fix_plain_text(plain: str, banned: list[str], before: str, after: str) -> str:
    """Replace configured banned characters in prose between protected URL tokens."""
    for char in banned:
        if char != "\u2014":
            plain = plain.replace(char, "-" if char == "\u2011" else "")
            continue
        pieces = []
        start = 0
        for match in _DASH_RUN.finditer(plain):
            left = plain[: match.start()] or before
            right = plain[match.end() :] or after
            pieces.append(plain[start : match.start()])
            pieces.append(_replace_dash_run(left, right, match.group()))
            start = match.end()
        pieces.append(plain[start:])
        plain = "".join(pieces)
    return plain


def fix_banned_characters(text: str, banned: list[str]) -> str:
    """Fix banned punctuation in prose while preserving whitespace-delimited URLs."""
    pieces = []
    start = 0
    for match in re.finditer(r"\S+", text):
        if not _is_url_token(match.group()):
            continue
        pieces.append(
            _fix_plain_text(
                text[start : match.start()],
                banned,
                text[:start],
                text[match.start() :],
            )
        )
        pieces.append(match.group())
        start = match.end()
    pieces.append(_fix_plain_text(text[start:], banned, text[:start], ""))
    return "".join(pieces)


def check_brand(text: str, rules: BrandRules) -> list[str]:
    errors = []
    for word in rules.banned_words:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.I):
            errors.append("banned_phrase:" + word)
    for char in rules.banned_characters:
        if char in text:
            errors.append("banned_character:" + char)
    if re.search(r"(?<!\w)#[\w]+", text):
        errors.append("hashtag")
    for link in extract_urls_with_indices(text):
        url = link["url"]
        domain = urlparse(url if "://" in url else "https://" + url).hostname
        if domain not in rules.allowed_links:
            errors.append("link_domain:" + str(domain))
    return errors


def check_leaked_source_ids(text: str, pack: ResearchPack) -> list[str]:
    """Report pack source ids that appear as whole tokens in tweet text."""
    return [
        f"leaked_source_id:{source.source_id}"
        for source in pack.sources
        if re.search(r"(?<![\w-])" + re.escape(source.source_id) + r"(?![\w-])", text)
    ]


def check_citations(draft: Draft, pack: ResearchPack) -> list[str]:
    if draft.slot_id != pack.slot_id:
        return ["slot_mismatch"]
    citable = {s.source_id for s in pack.sources if s.status == "ok" and s.evidence_text.strip()}
    return [
        f"tweet_{i}:citation:{source_id}"
        for i, t in enumerate(draft.tweets, 1)
        for source_id in t.source_ids
        if source_id not in citable
    ]


def validate(
    draft: Draft, pack: ResearchPack, spec: PostType, rules: BrandRules, limit: int = 280
) -> ValidationResult:
    if not 1 <= limit <= 280:
        raise ValueError("editorial limit must be 1..280")
    fixed = draft.model_copy(deep=True)
    fixes = []
    empty_content = []

    for i, tweet in enumerate(fixed.tweets, 1):
        text = tweet.text
        operations = {
            "banned_characters": partial(fix_banned_characters, banned=rules.banned_characters),
            "whitespace": lambda t: " ".join(t.split()),
            "curly_quotes": lambda t: t.translate(
                str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
            ),
            "trailing_hashtags": lambda t: re.sub(r"(?:^|\s+)#[\w]+(?:\s+#[\w]+)*\s*$", "", t),
        }
        for fix in sorted(rules.safe_fixes, key=lambda fix: fix != "banned_characters"):
            changed = operations[fix](text)
            if changed != text:
                fixes.append(f"tweet_{i}:{fix}")
            text = changed
        if not any(
            not ch.isspace() and not unicodedata.category(ch).startswith("P") for ch in text
        ):
            empty_content.append(f"tweet_{i}:invalid_text")
        tweet.text = f"{i}/{len(fixed.tweets)} {text}" if spec.numbering else text
    errors = check_citations(fixed, pack) + empty_content
    if not spec.units.min <= len(fixed.tweets) <= spec.units.max:
        errors.append("tweet_count")
    for i, tweet in enumerate(fixed.tweets, 1):
        errors.extend(f"tweet_{i}:{e}" for e in check_leaked_source_ids(tweet.text, pack))
        errors.extend(f"tweet_{i}:{e}" for e in check_brand(tweet.text, rules))
        parsed = parse_tweet(tweet.text)
        if not parsed.valid and f"tweet_{i}:invalid_text" not in errors:
            errors.append(f"tweet_{i}:invalid_text")
        if parsed.weightedLength > limit:
            errors.append(f"tweet_{i}:length")
    return ValidationResult(
        original=draft, normalized=fixed, fixes=fixes, violations=errors, accepted=not errors
    )


def length_feedback(result: ValidationResult, limit: int = 280) -> list[str]:
    """Writer-only measurements for length violations, so a retry knows how much to cut."""
    notes = []
    for i, tweet in enumerate(result.normalized.tweets, 1):
        if f"tweet_{i}:length" in result.violations:
            weighted = parse_tweet(tweet.text).weightedLength
            words = len(tweet.text.split())
            notes.append(
                f"tweet_{i}:weighted_length:{weighted}/{limit}:shorten_by_at_least:{weighted - limit}:words:{words}:max_words:{max(1, int(limit / 7.5))}"
            )
    return notes
