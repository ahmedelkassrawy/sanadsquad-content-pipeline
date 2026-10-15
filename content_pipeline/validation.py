import re
from urllib.parse import urlparse
from twitter_text import parse_tweet, extract_urls_with_indices
from .models import BrandRules, Draft, PostType, ResearchPack, ValidationResult


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


def check_citations(draft: Draft, pack: ResearchPack) -> list[str]:
    if draft.slot_id != pack.slot_id:
        return ["slot_mismatch"]
    citable = {s.source_id for s in pack.sources if s.status == "ok" and s.evidence_text.strip()}
    return [f"tweet_{i}:citation:{source_id}" for i, t in enumerate(draft.tweets, 1) for source_id in t.source_ids if source_id not in citable]


def validate(draft: Draft, pack: ResearchPack, spec: PostType, rules: BrandRules, limit: int = 280) -> ValidationResult:
    if not 1 <= limit <= 280:
        raise ValueError("editorial limit must be 1..280")
    fixed = draft.model_copy(deep=True)
    fixes = []
    empty_content = []
    for i, tweet in enumerate(fixed.tweets, 1):
        text = tweet.text
        operations = {
            "whitespace": lambda t: " ".join(t.split()),
            "curly_quotes": lambda t: t.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})),
            "trailing_hashtags": lambda t: re.sub(r"(?:^|\s+)#[\w]+(?:\s+#[\w]+)*\s*$", "", t),
        }
        for fix in rules.safe_fixes:
            changed = operations[fix](text)
            if changed != text:
                fixes.append(f"tweet_{i}:{fix}")
            text = changed
        if not text.strip():
            empty_content.append(f"tweet_{i}:invalid_text")
        tweet.text = f"{i}/{len(fixed.tweets)} {text}" if spec.numbering else text
    errors = check_citations(fixed, pack) + empty_content
    if not spec.units.min <= len(fixed.tweets) <= spec.units.max:
        errors.append("tweet_count")
    for i, tweet in enumerate(fixed.tweets, 1):
        errors.extend(f"tweet_{i}:{e}" for e in check_brand(tweet.text, rules))
        parsed = parse_tweet(tweet.text)
        if not parsed.valid and f"tweet_{i}:invalid_text" not in errors:
            errors.append(f"tweet_{i}:invalid_text")
        if parsed.weightedLength > limit:
            errors.append(f"tweet_{i}:length")
    return ValidationResult(original=draft, normalized=fixed, fixes=fixes, violations=errors, accepted=not errors)


def length_feedback(result: ValidationResult, limit: int = 280) -> list[str]:
    """Writer-only measurements for length violations, so a retry knows how much to cut."""
    notes = []
    for i, tweet in enumerate(result.normalized.tweets, 1):
        if f"tweet_{i}:length" in result.violations:
            weighted = parse_tweet(tweet.text).weightedLength
            words = len(tweet.text.split())
            notes.append(f"tweet_{i}:weighted_length:{weighted}/{limit}:shorten_by_at_least:{weighted - limit}:words:{words}:max_words:{max(1, int(limit / 7.5))}")
    return notes
