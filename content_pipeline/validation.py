import re
import unicodedata
from functools import partial
from twitter_text import parse_tweet
from skills import brand_rules
from .models import BrandRules, Draft, PostType, ResearchPack, ValidationResult
from .x_format import (
    CODE_SPAN,
    NUMBERING_PREFIX,
    is_url_token,
    numbering,
    split_overlong_tweets,
    strip_markdown,
)


# The brand check lives in the brand_rules skill; this alias keeps existing imports working.
check_brand = brand_rules.check_brand


_QUOTES = "\"'\u201c\u201d\u2018\u2019"
_OPENING_BRACKETS = "([{"
_CLOSING_PUNCTUATION = ",.;:!?)]}"
_PUNCTUATION = _CLOSING_PUNCTUATION + _OPENING_BRACKETS + _QUOTES
_DASH_RUN = re.compile(r"\s*\u2014(?:\s*\u2014)*\s*")
# Hyphen and en-dash look-alikes become a plain hyphen; only the em dash needs run handling.
_HYPHEN_LIKE = "\u2010\u2011\u2012\u2013\u2015\u2212"
# A number token not glued to a word or a decimal point (uint256, KB-01 and SWC-107 are not
# numbers here, but the second half of "0.8.0-0.9.5" is). A "v" before a dotted version is a
# prefix, so "v0.8.0" and "0.8.0" are the same number.
_NUMBER = re.compile(r"(?<![\w.])(?<![A-Za-z]-)(?:[vV](?=\d+\.\d))?(\d+(?:\.\d+)*)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_TOKEN = re.compile(r"\S+")
CLAIM_NOTE_MARKER = ":delete the clause containing"
LINK_NOTE_MARKER = ":X turns"
STAT_NOTE_MARKER = ":state"


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
            plain = plain.replace(char, "-" if char in _HYPHEN_LIKE else "")

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
        if not is_url_token(match.group()):
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


def check_leaked_source_ids(text: str, pack: ResearchPack) -> list[str]:
    """Report pack source ids that appear as whole tokens in tweet text."""
    return [
        f"leaked_source_id:{source.source_id}"
        for source in pack.sources
        if re.search(r"(?<![\w-])" + re.escape(source.source_id) + r"(?![\w-])", text)
    ]


def numbers_in(text: str) -> set[str]:
    """Number tokens in folded prose, ignoring URLs and thousands separators."""
    folded = unicodedata.normalize("NFKC", text)
    folded = _TOKEN.sub(lambda m: " " if is_url_token(m.group()) else m.group(), folded)

    return set(_NUMBER.findall(_THOUSANDS.sub("", folded)))


def check_numbers(text: str, pack: ResearchPack, focus: str | None = None) -> list[str]:
    """Report numbers that appear in no usable source of the pack, its topic or the slot focus."""
    allowed = numbers_in(pack.topic_name) | numbers_in(focus or "")

    for source in pack.sources:
        if source.status == "ok":
            allowed |= numbers_in(source.evidence_text)

    violations = []

    for number in sorted(numbers_in(text)):
        # Bare single digits ("step 1", "2 calls") are too common to gate; the reviewer judges them.
        if len(number) == 1:
            continue

        if number in allowed:
            continue

        # "0.8" is supported by evidence that says "0.8.0".
        supported = any(source.startswith(number + ".") for source in allowed)

        if not supported:
            violations.append(f"unsourced_number:{number}")

    return violations


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


# Opening words common enough that three tweets may share them.
_OPENING_STOPWORDS = frozenset(
    "a an and but for if in it so the this that to when you your we "
    # Numbered steps and closing checklists ("Step 1:", "Is ...?") legitimately repeat.
    "step is are does do can what how why".split()
)
REPEATED_OPENING_LIMIT = 3


def check_repeated_openings(texts: list[str]) -> list[str]:
    """Report a first word that opens three or more tweets of a thread ("OWASP says...")."""
    counts = {}

    # Distinct tweets only: the check is about varied openings across different points, and an
    # exact duplicate tweet is a different problem than repeated phrasing.
    for text in dict.fromkeys(NUMBERING_PREFIX.sub("", text) for text in texts):
        words = NUMBERING_PREFIX.sub("", unicodedata.normalize("NFKC", text)).split()

        if not words:
            continue

        first = words[0].strip(".,:;!?\"'()`").casefold()

        if first and first not in _OPENING_STOPWORDS:
            counts[first] = counts.get(first, 0) + 1

    return [
        f"repeated_opening:{word}"
        for word, count in counts.items()
        if count >= REPEATED_OPENING_LIMIT
    ]


# How many words after a number the stat's noun may come ("12 smart contract audits").
_STAT_NOUN_WINDOW = 4
_SENTENCE = re.compile(r"(?<=[.?!])\s+")
_WORDS = re.compile(r"[^\W_]+")
# Spelled-out numbers count too: "our three auditors" uses STAT-02's 3.
_NUMBER_WORDS = {
    word: str(value)
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _stat_head_noun(stat_text: str, number: str) -> str | None:
    """The noun a stat attaches to its number: the last word of the phrase after it.

    "has completed 12 smart contract audits." gives "audit"; "has 3 auditors." gives "auditor".
    """
    for clause in re.split(r"[.,;:]", stat_text.casefold()):
        words = [_NUMBER_WORDS.get(w, w) for w in _WORDS.findall(clause)]

        if number in words:
            phrase = words[words.index(number) + 1 : words.index(number) + 1 + _STAT_NOUN_WINDOW]

            return _singular(phrase[-1]) if phrase else None

    return None


def check_stat_claims(text: str, pack: ResearchPack) -> list[str]:
    """Keep approved firm stats as the separate facts they are.

    A number taken from a stat must keep that stat's noun within a few words ("12 smart contract
    auditors" or "12 auditors" fails, "12 completed audits" passes), and numbers from two
    different stats may not share a sentence ("our three auditors completed 12 audits" claims the
    three did all twelve). Spelled-out numbers count; numbers in code spans and links do not.
    """
    stats = [s for s in pack.sources if s.kind == "stat" and s.status == "ok"]

    if not stats:
        return []

    violations = []
    prose = CODE_SPAN.sub(" ", unicodedata.normalize("NFKC", text))
    prose = " ".join(" " if is_url_token(token) else token for token in prose.split())

    for sentence in _SENTENCE.split(prose):
        words = [_NUMBER_WORDS.get(w, w) for w in _WORDS.findall(sentence.casefold())]
        used = set()

        for index, word in enumerate(words):
            if not word.isdigit():
                continue

            owners = [s for s in stats if word in numbers_in(s.evidence_text)]

            if not owners:
                continue

            window = {_singular(w) for w in words[index + 1 : index + 1 + _STAT_NOUN_WINDOW]}
            bound = [s for s in owners if _stat_head_noun(s.evidence_text, word) in window]

            if bound:
                used.add(bound[0].source_id)
            else:
                following = words[index + 1] if index + 1 < len(words) else ""
                violations.append(f"stat_misbound:{word} {following}".rstrip())

        if len(used) > 1:
            violations.append("joined_stats:" + "+".join(sorted(used)))

    return violations


def check_claim_words(text: str, pack: ResearchPack, claim_words: list[str]) -> list[str]:
    """Report configured claim words that the text uses and no usable source of the pack does."""
    if not claim_words:
        return []

    # Code spans are code, not claims: `try/catch` is not a promise to catch anything.
    # Only paired spans count, so a stray backtick cannot hide the claims after it.
    prose = CODE_SPAN.sub(" ", text)
    folded = unicodedata.normalize("NFKC", prose)

    evidence = unicodedata.normalize(
        "NFKC", " ".join(source.evidence_text for source in pack.sources if source.status == "ok")
    )

    violations = []

    for word in dict.fromkeys(claim_words):
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"

        if re.search(pattern, folded, re.I) and not re.search(pattern, evidence, re.I):
            violations.append(f"unsupported_claim_word:{word}")

    return violations


def _apply_safe_fixes(text: str, index: int, rules: BrandRules, fixes: list[str]) -> str:
    """Apply the brand's safe fixes to one tweet and record each one that changed it."""
    operations = {
        "banned_characters": partial(fix_banned_characters, banned=rules.banned_characters),
        "whitespace": lambda t: " ".join(t.split()),
        "curly_quotes": lambda t: t.translate(
            str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
        ),
        "trailing_hashtags": lambda t: re.sub(r"(?:^|\s+)#[\w]+(?:\s+#[\w]+)*\s*$", "", t),
        "markdown": strip_markdown,
    }

    for fix in sorted(rules.safe_fixes, key=lambda fix: fix != "banned_characters"):
        changed = operations[fix](text)

        if changed != text:
            fixes.append(f"tweet_{index}:{fix}")

        text = changed

    return text


def validate(
    draft: Draft,
    pack: ResearchPack,
    spec: PostType,
    rules: BrandRules,
    limit: int = 280,
    focus: str | None = None,
) -> ValidationResult:
    if not 1 <= limit <= 280:
        raise ValueError("editorial limit must be 1..280")

    fixed = draft.model_copy(deep=True)
    fixes = []
    empty_content = []
    unsourced = []
    dangling = []
    claims = []

    for i, tweet in enumerate(fixed.tweets, 1):
        tweet.text = _apply_safe_fixes(tweet.text, i, rules, fixes)

    split_overlong_tweets(fixed, spec, limit, fixes)

    for i, tweet in enumerate(fixed.tweets, 1):
        text = tweet.text

        if not any(
            not ch.isspace() and not unicodedata.category(ch).startswith("P") for ch in text
        ):
            empty_content.append(f"tweet_{i}:invalid_text")

        # A lead-in with nothing after it ("then transfer:") means the content was left out.
        if text.endswith(":"):
            dangling.append(f"tweet_{i}:dangling_colon")

        # Checked before numbering, so the "3/8" prefix is not mistaken for a claim.
        unsourced.extend(f"tweet_{i}:{e}" for e in check_numbers(text, pack, focus))
        claims.extend(f"tweet_{i}:{e}" for e in check_claim_words(text, pack, spec.claim_words))
        claims.extend(f"tweet_{i}:{e}" for e in check_stat_claims(text, pack))

        tweet.text = numbering(spec, i, len(fixed.tweets)) + text

    errors = check_citations(fixed, pack) + empty_content + unsourced + dangling + claims

    if len(fixed.tweets) > 1:
        errors.extend(check_repeated_openings([tweet.text for tweet in fixed.tweets]))

    if not spec.units.min <= len(fixed.tweets) <= spec.units.max:
        errors.append("tweet_count")

    for i, tweet in enumerate(fixed.tweets, 1):
        errors.extend(f"tweet_{i}:{e}" for e in check_leaked_source_ids(tweet.text, pack))
        errors.extend(f"tweet_{i}:{e}" for e in brand_rules.check_brand(tweet.text, rules))

        parsed = parse_tweet(tweet.text)

        if not parsed.valid and f"tweet_{i}:invalid_text" not in errors:
            errors.append(f"tweet_{i}:invalid_text")

        if parsed.weightedLength > limit:
            errors.append(f"tweet_{i}:length")

    return ValidationResult(
        original=draft, normalized=fixed, fixes=fixes, violations=errors, accepted=not errors
    )


def colon_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for dangling colons, so a retry knows what to change."""
    return [
        f"{violation}:finish_the_sentence_or_put_the_code_inline_in_backticks_in_this_tweet"
        for violation in result.violations
        if violation.endswith(":dangling_colon")
    ]


def claim_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for unsupported claim words: delete the clause, do not reword the claim."""
    notes = []

    for violation in result.violations:
        _, separator, word = violation.partition(":unsupported_claim_word:")

        if separator:
            notes.append(
                f'{violation}{CLAIM_NOTE_MARKER} "{word}"; do not replace it with another '
                "general claim; a shorter post is fine"
            )

    return notes


def link_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for text that X would turn into a link, such as `target.call`."""
    notes = []

    for violation in result.violations:
        _, separator, domain = violation.partition(":link_domain:")

        if separator:
            notes.append(
                f'{violation}{LINK_NOTE_MARKER} "{domain}" into a link, even inside backticks; '
                "rewrite it with no dot between two words, for example "
                '"target.call" as "a low-level call to target"'
            )

    return notes


def stat_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for approved stats that were joined or given a new noun."""
    notes = []

    for violation in result.violations:
        if ":joined_stats:" in violation or ":stat_misbound:" in violation:
            notes.append(
                f"{violation}{STAT_NOTE_MARKER} each approved stat in its own sentence, with the "
                "number next to the same noun the stat uses (for example two sentences: one for "
                "the audit count, one for the team size)"
            )

    return notes


def is_writer_note(reason: str) -> bool:
    """Whether a feedback line is a writer-only instruction rather than a reason to publish."""
    return (
        (reason.startswith("tweet_") and ":weighted_length:" in reason)
        or CLAIM_NOTE_MARKER in reason
        or LINK_NOTE_MARKER in reason
        or STAT_NOTE_MARKER + " each approved stat" in reason
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
