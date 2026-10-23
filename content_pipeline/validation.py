import re
import unicodedata
from pathlib import Path
from functools import partial
from functools import lru_cache
from itertools import combinations
from twitter_text import parse_tweet
from skills import brand_rules
from .redaction import kb_protocol_name_violations
from .models import (
    BrandRules,
    Draft,
    MarketingFormat,
    PostType,
    ResearchPack,
    SourceRecord,
    ValidationResult,
)
from .x_format import (
    CODE_SPAN,
    NUMBERING_PREFIX,
    is_url_token,
    numbering,
    split_overlong_tweets,
    strip_markdown,
)
from . import ai_text as _ai_text
from twitter_text import extract_urls_with_indices
import yaml


# The brand check lives in the brand_rules skill; this alias keeps existing imports working.
check_brand = brand_rules.check_brand


_QUOTES = "\"'\u201c\u201d\u2018\u2019"
_OPENING_BRACKETS = "([{"
_CLOSING_PUNCTUATION = ",.;:!?)]}"
_PUNCTUATION = _CLOSING_PUNCTUATION + _OPENING_BRACKETS + _QUOTES
_DASH_RUN = re.compile(r"\s*\u2014(?:\s*\u2014)*\s*")
# Hyphen and en-dash look-alikes become a plain hyphen; only the em dash needs run handling.
_HYPHEN_LIKE = "\u2010\u2011\u2012\u2013\u2015\u2212"
_AI_TEXT_REJECTED_CHARACTERS = frozenset("\u2014\u2013\u2018\u2019\u201c\u201d")
# A number token not glued to a word or a decimal point (uint256, KB-01 and SWC-107 are not
# numbers here, but the second half of "0.8.0-0.9.5" is). A "v" before a dotted version is a
# prefix, so "v0.8.0" and "0.8.0" are the same number.
_NUMBER = re.compile(r"(?<![\w.])(?<![A-Za-z]-)(?:[vV](?=\d+\.\d))?(\d+(?:\.\d+)*)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_TOKEN = re.compile(r"\S+")
# A violation id never starts with this, so it marks a note for the writer and nothing else.
WRITER_NOTE_PREFIX = "writer_note:"


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
        if char in _AI_TEXT_REJECTED_CHARACTERS:
            continue

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


_DEFAULT_POLICY_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=None)
def _cta_patterns(root: Path) -> tuple[re.Pattern[str], ...]:
    """Load commercial CTA phrases; technical advice is deliberately not a CTA."""
    path = root / "inputs/marketing-cta.yaml"
    if not path.is_file():
        path = _DEFAULT_POLICY_ROOT / "inputs/marketing-cta.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    patterns = data.get("patterns") if isinstance(data, dict) else None
    if (
        not isinstance(patterns, list)
        or not patterns
        or not all(isinstance(p, str) for p in patterns)
    ):
        raise ValueError("marketing CTA patterns need nonempty string patterns")
    return tuple(re.compile(pattern, re.I) for pattern in patterns)


def check_marketing_format(
    draft: Draft, format: MarketingFormat | None, cta_allowed: bool, policy_root: Path | None = None
) -> list[str]:
    """Enforce a selected marketing format without making other post types opt in."""
    errors = []
    if format is not None:
        count = len(draft.tweets)
        if not format.units.min <= count <= format.units.max:
            errors.append(f"format_units:{format.id}:{count}/{format.units.min}-{format.units.max}")

        links = [
            link["url"] for tweet in draft.tweets for link in extract_urls_with_indices(tweet.text)
        ]
        if format.link_rule == "required" and not links:
            errors.append(f"format_link_required:{format.id}")
        if format.link_rule == "forbidden" and links:
            errors.append(f"format_link_forbidden:{format.id}")

    ctas = sum(
        len(pattern.findall(tweet.text))
        for tweet in draft.tweets
        for pattern in _cta_patterns(policy_root or _DEFAULT_POLICY_ROOT)
    )
    if ctas > 1:
        errors.append(f"marketing_cta_limit:{ctas}")
    elif ctas and not cta_allowed:
        errors.append("weekly_mix_cta_not_allowed")

    return errors


# These aliases are derived only when a pack source identifies the publisher in its title or URL.
# That keeps a protocol used as an example ("Per Uniswap pool") from becoming a source credit.
_CREDIT_SOURCE_MARKERS = {
    "consensys": ("consensys",),
    "openzeppelin": ("openzeppelin",),
    "owasp": ("owasp",),
    "solidity": ("solidity", "soliditylang"),
    "swc": ("swc-registry", "swcregistry", "smartcontractsecurity/swc"),
}


def _credit_source_names(pack: ResearchPack) -> set[str]:
    """Publisher names exposed by the pack's source titles and URLs."""
    names = set()

    for source in pack.sources:
        identity = unicodedata.normalize("NFKC", f"{source.title} {source.url or ''}").casefold()

        for name, markers in _CREDIT_SOURCE_MARKERS.items():
            if any(marker in identity for marker in markers):
                names.add(name)

    return names


# Publishers a thread may credit. Merged with the names found in the pack's own sources, these are
# the only names a lead-in credit ("per X", "according to X") counts for, so "Per Uniswap pool"
# is not a credit.
_KNOWN_PUBLISHERS = frozenset(
    "consensys openzeppelin owasp solidity swc mixbytes trail ethereum.org eip".split()
)
# A name, up to four words, then a crediting word: "OWASP also recommends", "The Solidity scoping
# documentation", "SWC registry says". A mention without one ("Before Solidity 0.8, overflow
# wrapped") is the subject of the tweet, not a credit. Each pattern is a lookahead, so every
# capitalized word is tried as the name: "In practice OWASP recommends" finds OWASP, not "In".
_CREDIT_WORDS = (
    "says|say|lists|list|recommends|recommend|suggests|suggest|warns|warn|notes|describes|"
    "describe|adds|defines|explains|states|documentation|docs|guidance"
)
_NAME = r"(?<![\w.-])([A-Z][\w.-]*)(?:['’]s)?"
_CREDIT_BEFORE_NAME = re.compile(
    r"(?=" + _NAME + r"(?:\s+[A-Za-z0-9][\w-]*){0,4}?\s+(?:" + _CREDIT_WORDS + r")\b)"
)
_CREDIT_AFTER_LEAD_IN = re.compile(
    r"\b(?i:per|according to|listed by|cited by|as described by)\s+(?:the\s+)?([A-Z][\w.-]*)"
)
# "In OWASP's words, ..." credits; "In Solidity 0.8, ..." does not.
_CREDIT_IN_WORDS = re.compile(r"\b(?i:in)\s+(?:the\s+)?([A-Z][\w.-]*)['’]s\s+words\b")
# "From OWASP: ..." credits only when the name is followed by a colon.
_CREDIT_FROM_COLON = re.compile(r"\b(?i:from)\s+(?:the\s+)?([A-Z][\w.-]*):")
# Verbs that also read as nouns ("External calls", "Red flags"); they only ever count for a
# publisher, which the name filter below enforces for every pattern.
_PUBLISHER_ONLY_CREDIT = re.compile(
    r"(?=" + _NAME + r"(?:\s+[A-Za-z0-9][\w-]*){0,4}?\s+(?:calls|flags|catalogs|ranks|names)\b)"
)
_ACRONYM = re.compile(r"^[A-Z]{2,}[A-Z0-9]*$")


def _credit_key(name: str) -> str:
    """One key per source: "SWC-107" and "SWC" are the same registry, so are EIP numbers."""
    key = name.rstrip(".").casefold()

    for prefix in ("swc", "eip"):
        if key.startswith(prefix):
            return prefix

    return key


def check_repeated_credits(texts: list[str], pack: ResearchPack) -> list[str]:
    """Report a source credited in more than one tweet of a thread.

    A credit is a source's name in crediting phrasing, not any mention of it. Only a source's
    name counts: a known publisher, a name from the pack's own sources, or an acronym such as
    OWASP. So "We recommend", "Developers ... docs" and "Uniswap V2 adds" are not credits, and
    "Per Uniswap pool" is not one either.
    """
    prose = [CODE_SPAN.sub(" ", unicodedata.normalize("NFKC", text)) for text in texts]
    publishers = _KNOWN_PUBLISHERS | _credit_source_names(pack)
    tweets_by_name = {}

    for index, text in enumerate(prose):
        names = set()

        for pattern in (
            _CREDIT_BEFORE_NAME,
            _CREDIT_AFTER_LEAD_IN,
            _CREDIT_IN_WORDS,
            _CREDIT_FROM_COLON,
            _PUBLISHER_ONLY_CREDIT,
        ):
            for match in pattern.finditer(text):
                raw = match.group(1)
                key = _credit_key(raw)

                if key in publishers or _ACRONYM.match(raw.rstrip(".")):
                    names.add(key)

        for name in names:
            tweets_by_name.setdefault(name, set()).add(index)

    return [
        f"repeated_credit:{name}"
        for name, tweets in sorted(tweets_by_name.items())
        if len(tweets) > 1
    ]


# A tweet that opens with a short lead-in and a colon ("Closing check:", "Root cause in one
# line:", "Mitigation #2:"). A word is plain text, so a backtick or URL scheme never matches.
_LABEL_WORD = r"(?:[A-Za-z0-9][A-Za-z0-9'’.-]*|#[0-9]+)"
LABEL_MAX_WORDS = 7
_SECTION_LABEL = re.compile(
    r"^(?:(TL;DR)|((?:"
    + _LABEL_WORD
    + r"\s+){0,"
    + str(LABEL_MAX_WORDS - 1)
    + r"}"
    + _LABEL_WORD
    + r")):(?!//)",
    re.I,
)
# A lead-in that starts with a verb is an instruction ("Check each function: ..."), not an outline.
_IMPERATIVE_STARTS = frozenset(
    "add ask avoid check compare confirm consider do don't ensure fix grep keep look make mitigate "
    "never prefer put read remember remove replace review run search set test to try update use "
    "verify watch write".split()
)
# A lead-in with a verb is a sentence that happens to use a colon ("The fix is X: do Y"), not a
# label such as "Root cause in one line".
_LEAD_IN_VERBS = frozenset(
    "is are was were be been has have had does did can could will would should must may might "
    "also follows starts fixes lists recommends recommend suggests describes appear appears "
    "means lets allows needs requires shows uses trusts becomes".split()
)
_LABEL_PHRASES = frozenset("code pattern|why this is dangerous|tl;dr".split("|"))


_QUESTION_WORDS = frozenset("why what how when where which who".split())
# Subjects that make a lead-in a clause ("You need two checks:", "We recommend:").
_PRONOUN_SUBJECTS = frozenset("i we you they he she it".split())
# Verbs that are past tense without -ed, and crediting verbs, so "The DAO lost 3.6M ETH:" and
# "OWASP says:" read as clauses.
_IRREGULAR_VERBS = frozenset(
    "lost made took found ran broke left sent got gave became held paid stole won saw knew said "
    "says say warns explains adds defines".split()
)
# Words that are a noun in a label ("Release notes") but a verb after a source ("SWC entry notes").
_NOUN_OR_VERB = frozenset("notes states needs".split())
_SOURCE_NOUNS = frozenset(
    "owasp swc solidity openzeppelin entry registry docs documentation spec specification guide "
    "eip team auditor report".split()
)
# "Areas to focus on", "Mitigations per OWASP": a preposition after a plural is not its verb.
_PREPOSITIONS = frozenset(
    "to of for in on at with from by about into under over worth per against like across via "
    "without within through".split()
)
# Words before a plural noun that make it a counted noun, not a verb: "two things", "more entries".
_QUANTIFIERS = frozenset(
    "a an the this that these those our your their its my one two three four five six more "
    "other few several many some all each every any no".split()
)


def _has_finite_verb(words: list[str]) -> bool:
    """Whether a short lead-in reads as a clause rather than a noun-phrase label.

    The rule is structural, not a list fitted to examples:
    - a question-word heading ("Why it happens", "What changed") is a label;
    - an auxiliary or a common finite verb ("The fix is X", "OWASP lists") makes it a clause;
    - a plural subject followed by a plain verb ("Callbacks reenter", "Callbacks run first")
      is a clause, but not when the second word is a participle ("Lessons learned");
    - a pronoun subject ("You need two checks") makes it a clause;
    - a past-tense verb with words after it ("Attackers drained the pool", "This pattern caused
      the DAO hack") makes it a clause; a final participle ("Lessons learned") does not;
    - a singular -s verb in the middle after a noun ("OWASP names four areas") makes it a clause;
    - anything else, including plural-noun phrases ("Common pitfalls", "Attack vectors",
      "Other mitigations"), is a label. A trailing -s word is the label's plural noun.
    Known limit: an adjective before a plural with words after it ("Common pitfalls in lending
    protocols") reads as a clause, so that label passes; failing that way costs nothing.
    """
    if words[0] in _QUESTION_WORDS:
        return False

    if words[0] in _PRONOUN_SUBJECTS:
        return True

    if _LEAD_IN_VERBS.intersection(words) or _IRREGULAR_VERBS.intersection(words[1:]):
        return True

    # "notes" and "states" are verbs after a source ("The SWC entry notes") and nouns otherwise
    # ("Release notes", "Contract states").
    for index in range(1, len(words)):
        if words[index] in _NOUN_OR_VERB and words[index - 1] in _SOURCE_NOUNS:
            return True

    # A plural, then a participle, then a preposition or nothing is a noun phrase ("Lessons
    # learned from the DAO", "Bugs caused by reentrancy", "Areas needing review"); a past tense
    # with an object is a clause ("Attackers drained the pool").
    if len(words) >= 2 and _is_plural_noun(words[0]) and words[1].endswith(("ed", "ing")):
        return words[1].endswith("ed") and len(words) > 2 and words[2] not in _PREPOSITIONS

    # A past-tense verb with an object ("This pattern caused the DAO hack"). Short words that
    # only look like -ed verbs ("feed", "red", "need") do not count, and a following preposition
    # makes it a participle phrase ("Issues related to reentrancy").
    for index in range(1, len(words) - 1):
        word = words[index]

        if (
            word.endswith("ed")
            and len(word) >= 5
            and words[index - 1] not in _QUANTIFIERS
            and words[index + 1] not in _PREPOSITIONS
        ):
            return True

    if len(words) >= 2 and _is_plural_noun(words[0]):
        second = words[1]

        # A participle after a plural ("Lessons learned", "Areas needing review") is a noun phrase.
        return (
            not second.endswith(("ed", "ing"))
            and second not in _DETERMINERS
            and second not in _PREPOSITIONS
            and not second.isdigit()
        )

    # A singular verb in the middle of the lead-in ("OWASP names four areas", "Reentrancy comes
    # from ...", "The attack fits in ..."): an -s word after a noun, with words still following.
    # A trailing -s word is the label's plural noun ("Common pitfalls"), and one after a number or
    # quantifier is a plural ("Two more entries on the list").
    for index in range(1, len(words) - 1):
        word, previous = words[index], words[index - 1]

        if (
            _is_plural_noun(word)
            and previous not in _QUANTIFIERS
            and not previous.isdigit()
            and not _is_plural_noun(previous)
        ):
            return True

    return False


def _section_label(text: str) -> str | None:
    """The outline label a tweet opens with, or None when its first words are the point."""
    body = NUMBERING_PREFIX.sub("", text)
    match = _SECTION_LABEL.match(body)

    if not match:
        return None

    label = (match.group(1) or match.group(2)).casefold()
    words = label.split()

    if label in _LABEL_PHRASES:
        return label

    if words[0] in _IMPERATIVE_STARTS or _has_finite_verb(words):
        return None

    return label


def check_section_labels(texts: list[str]) -> list[str]:
    """Report tweets that open with an outline label instead of their point."""
    violations = []

    for index, text in enumerate(texts, 1):
        label = _section_label(text)

        if label:
            violations.append(f"tweet_{index}:section_label:{label}")

    return violations


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
        # "OWASP's" opens with the same word as "OWASP".
        first = re.sub(r"['’]s$", "", first)

        if first and first not in _OPENING_STOPWORDS:
            counts[first] = counts.get(first, 0) + 1

    return [
        f"repeated_opening:{word}"
        for word, count in counts.items()
        if count >= REPEATED_OPENING_LIMIT
    ]


_REPEAT_STOPWORDS = frozenset(
    "a an and are as at be before but by can could do does for from had has have if in into is "
    "it its may more must not of on or our should so than that the their them then there these "
    "they this those to under up use used using was we when where which while who will with "
    "would you your".split()
)
_REPEAT_STEMS = {
    "clearing": "clear",
    "reducing": "reduce",
    "repeatedly": "repeat",
    "sending": "send",
    "sends": "send",
    "sent": "send",
    "withdrawal": "withdraw",
    "withdrawals": "withdraw",
}
REPEATED_POINT_MIN_OVERLAP = 0.35
REPEATED_POINT_HIGH_OVERLAP = 0.5
REPEATED_POINT_MIN_SHARED = 7
REPEATED_POINT_MIN_BIGRAMS = 2
REPEATED_POINT_MAX_DISTANCE = 2
_EXAMPLE_MARKER = re.compile(
    r"(?i)(?:\b(?:case|concretely|example|picture|scenario)\b|^(?:take|typical case)\b)"
)


def _repeat_stem(word: str) -> str:
    if word in _REPEAT_STEMS:
        return _REPEAT_STEMS[word]

    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"

    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]

    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]

    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]

    return word


def _repeat_words(text: str) -> list[str]:
    """Lightly stemmed content words used to compare two tweets."""
    prose = NUMBERING_PREFIX.sub("", CODE_SPAN.sub(" ", unicodedata.normalize("NFKC", text)))

    return [
        _repeat_stem(word)
        for word in re.findall(r"[a-z][a-z0-9]+", prose.casefold())
        if len(word) >= 3 and word not in _REPEAT_STOPWORDS
    ]


def repeated_point_score(left: str, right: str) -> tuple[float, int, int]:
    """Overlap coefficient, shared words and shared adjacent phrases for two tweets."""
    left_words = _repeat_words(left)
    right_words = _repeat_words(right)
    left_set = set(left_words)
    right_set = set(right_words)

    if not left_set or not right_set:
        return 0.0, 0, 0

    shared = left_set & right_set
    left_bigrams = set(zip(left_words, left_words[1:]))
    right_bigrams = set(zip(right_words, right_words[1:]))

    return (
        len(shared) / min(len(left_set), len(right_set)),
        len(shared),
        len(left_bigrams & right_bigrams),
    )


def check_repeated_points(texts: list[str]) -> list[str]:
    """Report a non-closing tweet that repeats an earlier thread point or example."""
    violations = []

    # A final checklist or question may deliberately recap the opening. Compare the substantive
    # body tweets only; repeated openings have their own separate check.
    for left, right in combinations(range(max(0, len(texts) - 1)), 2):
        score, shared, bigrams = repeated_point_score(texts[left], texts[right])

        if shared < REPEATED_POINT_MIN_SHARED or score < REPEATED_POINT_MIN_OVERLAP:
            continue

        left_is_example = bool(_EXAMPLE_MARKER.search(NUMBERING_PREFIX.sub("", texts[left])))
        right_is_example = bool(_EXAMPLE_MARKER.search(NUMBERING_PREFIX.sub("", texts[right])))

        if left_is_example != right_is_example:
            continue

        repeated_example = (
            left_is_example and right_is_example and bigrams >= REPEATED_POINT_MIN_BIGRAMS
        )
        repeated_point = (
            not left_is_example
            and score >= REPEATED_POINT_HIGH_OVERLAP
            and shared >= REPEATED_POINT_MIN_SHARED + 1
            and bigrams >= 1
            and right - left <= REPEATED_POINT_MAX_DISTANCE
        )

        if not repeated_example and not repeated_point:
            continue

        violations.append(f"tweet_{right + 1}:repeats_tweet:{left + 1}")

    return violations


# How many words after a number the stat's noun may come ("12 smart contract audits").
_STAT_NOUN_WINDOW = 5
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


# Words that end in "s" without being a plural noun, so they cannot be what a number counts.
_NOT_PLURAL = frozenset("this does always across thus plus less various perhaps towards".split())


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _is_plural_noun(word: str) -> bool:
    return _singular(word) != word and word not in _NOT_PLURAL


def _sentence_words(sentence: str) -> tuple[list[str], set[int]]:
    """Folded words of a sentence, and the indexes of words glued to a number by a hyphen.

    "A 3-auditor team" gives the words a, 3, auditor, team and the glued index {2}.
    """
    words = []
    glued = set()
    previous_end = -1

    for match in _WORDS.finditer(sentence.casefold()):
        # A number that is part of a dotted version ("0.8.12") is not a count. A number that ends
        # a sentence ("We have completed 12.") still is.
        start, end = match.start(), match.end()
        in_version = (
            start >= 2 and sentence[start - 1] == "." and sentence[start - 2].isdigit()
        ) or (end + 1 < len(sentence) and sentence[end] == "." and sentence[end + 1].isdigit())

        if match.group().isdigit() and in_version:
            continue

        word = _NUMBER_WORDS.get(match.group(), match.group())

        if words and words[-1].isdigit() and match.start() == previous_end + 1:
            if sentence[previous_end] == "-":
                glued.add(len(words))

        words.append(word)

        previous_end = match.end()

    return words, glued


def _counted_noun(words: list[str], index: int, glued: set[int]) -> str | None:
    """The noun the number at words[index] counts, skipping adjectives and compound modifiers.

    "12 smart contract audits" counts audits, not "smart" or "contract"; "12 auditors review every
    audit" counts auditors, not the later audit; "a 3-auditor team" counts the auditor. None when
    no plural noun follows within the window, so the caller cannot tell.
    """
    if index + 1 in glued:
        return _singular(words[index + 1])

    for word in words[index + 1 : index + 1 + _STAT_NOUN_WINDOW]:
        if _is_plural_noun(word):
            return _singular(word)

    return None


def _stat_head_noun(stat_text: str, number: str) -> str | None:
    """The noun a stat attaches to its number.

    "has completed 12 smart contract audits." gives "audit"; "has 3 auditors." gives "auditor".
    """
    for clause in re.split(r"[.,;:]", stat_text.casefold()):
        words, glued = _sentence_words(clause)

        if number not in words:
            continue

        index = words.index(number)
        counted = _counted_noun(words, index, glued)

        if counted:
            return counted

        phrase = words[index + 1 : index + 1 + _STAT_NOUN_WINDOW]

        return _singular(phrase[-1]) if phrase else None

    return None


def _binds_stat(
    stat: SourceRecord, number: str, words: list[str], index: int, glued: set[int]
) -> bool:
    """Whether the number at words[index] sits on the noun that the stat gives that number."""
    head = _stat_head_noun(stat.evidence_text, number)
    counted = _counted_noun(words, index, glued)

    if counted:
        return counted == head

    # No plural noun to bind to ("12 audit" in a clause with no plural): accept the stat's noun
    # anywhere in the window, as the check always did.
    return head in {_singular(w) for w in words[index + 1 : index + 1 + _STAT_NOUN_WINDOW]}


# Nouns a firm claim counts: people and the firm's work. A stat number on any of these must
# match its stat; a stat number on another noun ("3 functions") is a technical count.
_FIRM_NOUNS = frozenset(
    "auditor engineer researcher reviewer expert specialist member analyst employee person "
    "people staff developer audit review assessment engagement client customer project "
    "protocol team firm year report finding".split()
)
_FIRM_VERBS = frozenset(
    "completed finished delivered shipped audited reviewed done conducted performed ran run "
    "counting".split()
)
_MONTHS = frozenset(
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec".split()
)
_TIME_UNITS = frozenset(
    "am pm minute minutes min hour hours hr day days week weeks month months second seconds".split()
)
_ORDINAL_LABELS = frozenset("step part phase stage tweet line item version".split())
# Qualifiers that make an exact approved count approximate or a bound.
_APPROXIMATE_BEFORE = frozenset(
    "almost nearly over about around roughly approximately some circa under".split()
)
_BOUND_BEFORE = (["at", "least"], ["more", "than"], ["less", "than"], ["up", "to"], ["at", "most"])
# Outcome verbs that turn "all 12 audits" into a claim about what every audit found.
_OUTCOME_WORDS = frozenset(
    "found uncovered discovered caught identified revealed prevented stopped flagged".split()
)
_DETERMINERS = frozenset("our the its their a an this that these those my your".split())


def _stat_distortion(words: list[str], index: int) -> str | None:
    """A qualifier that turns an approved count into a different claim."""
    before = words[max(0, index - 4) : index]
    after = words[index + 1 : index + 8]

    if before[-1:] and before[-1] in _APPROXIMATE_BEFORE:
        return before[-1]

    if before[-2:] in _BOUND_BEFORE:
        return "_".join(before[-2:])

    # "12 audits per auditor" divides the count; "per our records" is only attribution.
    if "per" in after:
        following = after[after.index("per") + 1 : after.index("per") + 2]

        if following and following[0] not in _DETERMINERS:
            return "per"

    # "All 12 audits found critical bugs", "Every one of our 12 audits uncovered ...".
    if {"all", "every"}.intersection(before) and _OUTCOME_WORDS.intersection(after):
        return "all_found"

    return None


def check_stat_claims(text: str, pack: ResearchPack) -> list[str]:
    """Keep approved firm stats as the separate facts they are.

    A number taken from a stat must count the noun the stat gives it ("12 smart contract
    auditors" or "12 auditors" fails, "12 completed audits" passes; "12 auditors review every
    audit" fails, whatever noun comes later in the sentence), and numbers from two
    different stats may not share a sentence ("our three auditors completed 12 audits" claims the
    three did all twelve). Qualifiers such as "over" and "per auditor" fail. Spelled-out numbers
    count; unrelated technical counts, dotted versions, code spans and links do not.
    """
    stats = [s for s in pack.sources if s.kind == "stat" and s.status == "ok"]

    if not stats:
        return []

    violations = []
    prose = CODE_SPAN.sub(" ", unicodedata.normalize("NFKC", text))
    prose = " ".join(" " if is_url_token(token) else token for token in prose.split())
    stat_numbers = {number for stat in stats for number in numbers_in(stat.evidence_text)}
    # The firm's own name marks a sentence as a firm claim ("Sentinel Grove has done 12 so far").
    brand_words = {word for stat in stats for word in _WORDS.findall(stat.title.casefold())}

    # "12+ audits" turns an exact count into a lower bound; the word split below drops the "+".
    # Only a count of firm work counts ("Node 12+" is a version requirement).
    for match in re.finditer(r"(?<![\w.])(\d+)\+((?:\s+[A-Za-z-]+){1,4})", prose):
        following = {_singular(word) for word in match.group(2).casefold().split()}

        if match.group(1) in stat_numbers and following & _FIRM_NOUNS:
            violations.append(f"stat_distorted:{match.group(1)}:plus")

    for sentence in _SENTENCE.split(prose):
        words, glued = _sentence_words(sentence)
        used = set()

        for index, word in enumerate(words):
            if not word.isdigit():
                continue

            owners = [s for s in stats if word in numbers_in(s.evidence_text)]

            if not owners:
                continue

            # "Step 3", "part 12": the number labels a position, it counts nothing.
            if index and words[index - 1] in _ORDINAL_LABELS:
                continue

            # A stat number counting a technical noun ("3 functions", "3 things") is not a firm
            # claim. A stat number counting people or firm work ("12 engineers", "3 reviewers"),
            # or counting nothing ("We have completed 12."), must match the stat.
            counted = _counted_noun(words, index, glued)

            if counted and counted not in _FIRM_NOUNS:
                continue

            # A number that counts nothing is a firm claim only in a sentence about the firm's
            # work ("We have completed 12.", "Our auditors number 12."). Dates, times and
            # durations ("before March 12", "at 3 pm", "a 3 minute read") and bare picks
            # ("Pick three.") are not.
            if counted is None:
                previous = words[index - 1] if index else ""
                following = words[index + 1] if index + 1 < len(words) else ""
                firm_context = any(
                    _singular(other) in _FIRM_NOUNS or other in _FIRM_VERBS or other in brand_words
                    for other in words
                )

                if previous in _MONTHS or following in _TIME_UNITS or not firm_context:
                    continue

            bound = [s for s in owners if _binds_stat(s, word, words, index, glued)]

            if bound:
                used.add(bound[0].source_id)

                if distortion := _stat_distortion(words, index):
                    violations.append(f"stat_distorted:{word}:{distortion}")
            else:
                following = words[index + 1] if index + 1 < len(words) else ""
                violations.append(f"stat_misbound:{word} {following}".rstrip())

        if len(used) > 1:
            violations.append("joined_stats:" + "+".join(sorted(used)))

    return violations


_STAT_SIGNATURE_STOPWORDS = _REPEAT_STOPWORDS | frozenset(
    "code contract from grove sentinel smart team teams their our".split()
)
_BRAND_WORD = re.compile(r"[a-z][a-z0-9]+")
APPROVED_STAT_SIGNATURE_OVERLAP = 0.6


def _stat_signature_words(text: str) -> set[str]:
    return {
        _repeat_stem(word)
        for word in _BRAND_WORD.findall(unicodedata.normalize("NFKC", text).casefold())
        if word not in _STAT_SIGNATURE_STOPWORDS
    }


def approved_stats_used(text: str, pack: ResearchPack) -> set[str]:
    """Approved firm-stat ids whose distinctive fact appears in the post."""
    prose_words, _ = _sentence_words(text)
    prose_set = {_singular(word) for word in prose_words}
    used = set()

    for stat in (source for source in pack.sources if source.kind == "stat"):
        numbers = numbers_in(stat.evidence_text)

        if numbers:
            if any(
                number in prose_words and _stat_head_noun(stat.evidence_text, number) in prose_set
                for number in numbers
            ):
                used.add(stat.source_id)

            continue

        signature = _stat_signature_words(stat.evidence_text)

        if not signature:
            continue

        overlap = len(signature & _stat_signature_words(text)) / len(signature)

        if overlap >= APPROVED_STAT_SIGNATURE_OVERLAP:
            used.add(stat.source_id)

    return used


def check_post_limits(
    texts: list[str], pack: ResearchPack, spec: PostType, brand_name: str
) -> list[str]:
    """Apply optional post-type limits to the full post, not individual tweets."""
    text = " ".join(texts)
    violations = []

    if spec.max_approved_stats is not None:
        count = len(approved_stats_used(text, pack))

        if count > spec.max_approved_stats:
            violations.append(f"approved_stat_limit:{count}/{spec.max_approved_stats}")

    if spec.max_brand_mentions is not None:
        count = len(
            re.findall(
                r"(?<!\w)" + re.escape(brand_name) + r"(?!\w)",
                unicodedata.normalize("NFKC", text),
                re.I,
            )
        )

        if count > spec.max_brand_mentions:
            violations.append(f"brand_mention_limit:{count}/{spec.max_brand_mentions}")

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
        "trailing_hashtags": lambda t: re.sub(r"(?:^|\s+)#[\w]+(?:\s+#[\w]+)*\s*$", "", t),
        "markdown": strip_markdown,
    }

    for fix in sorted(rules.safe_fixes, key=lambda fix: fix != "banned_characters"):
        if fix == "curly_quotes":
            continue

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
    ai_policy_root: Path | None = None,
    format: MarketingFormat | None = None,
    cta_allowed: bool = True,
) -> ValidationResult:
    if not 1 <= limit <= 280:
        raise ValueError("editorial limit must be 1..280")

    fixed = draft.model_copy(deep=True)
    fixes = []
    empty_content = []
    unsourced = []
    dangling = []
    claims = []
    ai_violations = _ai_text.check_ai_text(
        "\n".join(tweet.text for tweet in draft.tweets), ai_policy_root
    )
    protocol_names = kb_protocol_name_violations(
        "\n".join(tweet.text for tweet in draft.tweets), ai_policy_root
    )

    for i, tweet in enumerate(fixed.tweets, 1):
        tweet.text = _apply_safe_fixes(tweet.text, i, rules, fixes)

    split_overlong_tweets(fixed, spec, limit, fixes, format)

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

        tweet.text = numbering(spec, i, len(fixed.tweets), format) + text

    errors = (
        ai_violations
        + protocol_names
        + check_citations(fixed, pack)
        + empty_content
        + unsourced
        + dangling
        + claims
    )
    errors.extend(
        brand_rules.check_research_separation(
            fixed, pack, spec, enforce_marketing_web=format is not None
        )
    )
    errors.extend(
        check_post_limits([tweet.text for tweet in fixed.tweets], pack, spec, rules.brand_name)
    )

    if len(fixed.tweets) > 1:
        texts = [tweet.text for tweet in fixed.tweets]
        errors.extend(check_repeated_openings(texts))
        errors.extend(check_repeated_credits(texts, pack))
        errors.extend(check_repeated_points(texts))
        errors.extend(check_section_labels(texts))

    units = format.units if format else spec.units
    if not units.min <= len(fixed.tweets) <= units.max:
        errors.append("tweet_count")

    errors.extend(check_marketing_format(fixed, format, cta_allowed, ai_policy_root))

    for i, tweet in enumerate(fixed.tweets, 1):
        errors.extend(f"tweet_{i}:{e}" for e in check_leaked_source_ids(tweet.text, pack))
        errors.extend(f"tweet_{i}:{e}" for e in brand_rules.check_brand(tweet.text, rules, pack))

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
        f"{WRITER_NOTE_PREFIX}{violation}:"
        "finish_the_sentence_or_put_the_code_inline_in_backticks_in_this_tweet"
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
                f'{WRITER_NOTE_PREFIX}{violation}:delete the clause containing "{word}"; '
                "do not replace it with another "
                "general claim; a shorter post is fine"
            )

    return notes


def link_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for text that X would turn into a link, such as `target.call`."""
    notes = []

    for violation in result.violations:
        _, separator, domain = violation.partition(":link_domain:")
        if not separator:
            _, separator, domain = violation.partition(":link_not_fetched:")

        if separator:
            notes.append(
                f'{WRITER_NOTE_PREFIX}{violation}:X turns "{domain}" into a link, even inside '
                "backticks; "
                "rewrite it with no dot between two words, for example "
                '"target.call" as "a low-level call to target"'
            )

    return notes


def stat_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for approved stats that were joined or given a new noun."""
    notes = []

    for violation in result.violations:
        if any(
            marker in violation
            for marker in (":joined_stats:", ":stat_misbound:", ":stat_distorted:")
        ):
            notes.append(
                f"{WRITER_NOTE_PREFIX}{violation}:state each approved stat exactly as the pack "
                "words it, in its own sentence, with its number next to the same noun the stat "
                "uses and no qualifier such as over, about or per"
            )

    return notes


def credit_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for a source credited in more than one tweet."""
    return [
        f"{WRITER_NOTE_PREFIX}{violation}:name this source once in the thread; in other tweets "
        "state its facts directly, without naming it again"
        for violation in result.violations
        if violation.startswith("repeated_credit:")
    ]


def post_limit_feedback(result: ValidationResult, brand_name: str = "the brand") -> list[str]:
    """Writer-only notes for configured whole-post fact and brand-name limits.

    The limit is read from the violation itself ("approved_stat_limit:3/2" allows 2).
    """
    notes = []

    for violation in result.violations:
        name, _, counts = violation.partition(":")
        limit = counts.partition("/")[2]

        if name == "approved_stat_limit":
            notes.append(
                f"{WRITER_NOTE_PREFIX}{violation}:delete a firm fact; keep at most {limit} approved "
                "firm stats and connect what remains to what the reviewing team gets"
            )

        if name == "brand_mention_limit":
            notes.append(
                f"{WRITER_NOTE_PREFIX}{violation}:name {brand_name} at most {limit} time(s); use "
                '"we" or "our team" after that'
            )

    return notes


def label_feedback(result: ValidationResult) -> list[str]:
    """Writer-only note for a tweet that opens with an outline label."""
    return [
        f"{WRITER_NOTE_PREFIX}{violation}: drop the label and open the tweet with its point (for "
        "example, write the root cause as a sentence in your own words)"
        for violation in result.violations
        if ":section_label:" in violation
    ]


def is_writer_note(reason: str) -> bool:
    """Whether a feedback line is a writer-only instruction rather than a reason to publish."""
    return reason.startswith(WRITER_NOTE_PREFIX)


def length_feedback(result: ValidationResult, limit: int = 280) -> list[str]:
    """Writer-only measurements for length violations, so a retry knows how much to cut."""
    notes = []

    for i, tweet in enumerate(result.normalized.tweets, 1):
        if f"tweet_{i}:length" in result.violations:
            weighted = parse_tweet(tweet.text).weightedLength
            words = len(tweet.text.split())

            notes.append(
                f"{WRITER_NOTE_PREFIX}tweet_{i}:weighted_length:{weighted}/{limit}:"
                f"shorten_by_at_least:{weighted - limit}:words:{words}:"
                f"max_words:{max(1, int(limit / 7.5))}"
            )

    return notes
