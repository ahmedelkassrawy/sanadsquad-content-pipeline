import re
import unicodedata
from functools import partial
from itertools import combinations
from twitter_text import parse_tweet
from skills import brand_rules
from .models import BrandRules, Draft, PostType, ResearchPack, SourceRecord, ValidationResult
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


def check_repeated_credits(texts: list[str], pack: ResearchPack) -> list[str]:
    """Report a pack source named in more than one tweet of a thread."""
    tweets_by_name = {}
    source_names = _credit_source_names(pack)

    for index, text in enumerate(texts):
        prose = CODE_SPAN.sub(" ", unicodedata.normalize("NFKC", text)).casefold()

        for name in source_names:
            if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", prose):
                tweets_by_name.setdefault(name, set()).add(index)

    return [
        f"repeated_credit:{name}"
        for name, tweets in sorted(tweets_by_name.items())
        if len(tweets) > 1
    ]


# A tweet that opens with a short lead-in and a colon ("Closing check:", "Root cause in one
# line:", "Mitigation #2:"). A word is plain text, so a backtick or URL scheme never matches.
_LABEL_WORD = r"(?:[A-Za-z0-9][A-Za-z0-9'’-]*|#[0-9]+)"
LABEL_MAX_WORDS = 5
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
    "means lets allows needs requires shows uses trusts reenter become".split()
)
_LABEL_PHRASES = frozenset("code pattern|why this is dangerous|tl;dr".split("|"))
_NON_VERB_S_WORDS = frozenset("areas causes checks focus forms shares steps things".split())
_SUBJECT_MODIFIERS = frozenset(
    "a an the this that these those one two three four five more concrete useful key closing".split()
)


def _has_finite_verb(words: list[str]) -> bool:
    """Whether a short lead-in reads as a clause rather than a noun-phrase label."""
    if _LEAD_IN_VERBS.intersection(words):
        return True

    for index, word in enumerate(words[1:], 1):
        previous = words[index - 1]

        if previous in _SUBJECT_MODIFIERS or word in _NON_VERB_S_WORDS:
            continue

        if re.search(r"(?:s|ed)$", word):
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
        if match.group().isdigit() and (
            (match.start() and sentence[match.start() - 1] == ".")
            or (match.end() < len(sentence) and sentence[match.end()] == ".")
        ):
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


def _stat_distortion(words: list[str], index: int) -> str | None:
    """A qualifier that turns an approved count into a different claim."""
    before = words[max(0, index - 2) : index]
    after = words[index + 1 : index + 8]

    if before[-1:] and before[-1] in {"almost", "nearly", "over"}:
        return before[-1]

    if before[-2:] in (["at", "least"], ["more", "than"], ["less", "than"]):
        return "_".join(before[-2:])

    if "per" in after:
        return "per"

    if before[-1:] and before[-1] in {"all", "every"} and "found" in after:
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
    stat_heads = {
        head
        for stat in stats
        for number in numbers_in(stat.evidence_text)
        if (head := _stat_head_noun(stat.evidence_text, number))
    }

    for sentence in _SENTENCE.split(prose):
        words, glued = _sentence_words(sentence)
        used = set()

        for index, word in enumerate(words):
            if not word.isdigit():
                continue

            owners = [s for s in stats if word in numbers_in(s.evidence_text)]

            if not owners:
                continue

            counted = _counted_noun(words, index, glued)
            window = {
                _singular(candidate)
                for candidate in words[index + 1 : index + 1 + _STAT_NOUN_WINDOW]
            }
            owner_heads = {
                head for owner in owners if (head := _stat_head_noun(owner.evidence_text, word))
            }

            # A pack may include the firm's count of 3 auditors while the technical prose says
            # "3 functions" or "step 3". Only enforce the stat when the count is attached to a
            # firm-stat noun, or when that noun appears in the number's short phrase.
            if counted not in stat_heads and not owner_heads.intersection(window):
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
    errors.extend(
        check_post_limits([tweet.text for tweet in fixed.tweets], pack, spec, rules.brand_name)
    )

    if len(fixed.tweets) > 1:
        texts = [tweet.text for tweet in fixed.tweets]
        errors.extend(check_repeated_openings(texts))
        errors.extend(check_repeated_credits(texts, pack))
        errors.extend(check_repeated_points(texts))
        errors.extend(check_section_labels(texts))

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
                f"{WRITER_NOTE_PREFIX}{violation}:state each approved stat exactly, in its own "
                "sentence, with the "
                "number next to the same noun the stat uses (for example two sentences: one for "
                "the audit count, one for the team size)"
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


def post_limit_feedback(result: ValidationResult) -> list[str]:
    """Writer-only notes for configured whole-post fact and brand-name limits."""
    notes = []

    for violation in result.violations:
        if violation.startswith("approved_stat_limit:"):
            notes.append(
                f"{WRITER_NOTE_PREFIX}{violation}:delete one firm fact; keep at most two approved "
                "firm stats and connect the remaining fact to what the reviewing team gets"
            )

        if violation.startswith("brand_mention_limit:"):
            notes.append(
                f'{WRITER_NOTE_PREFIX}{violation}:use "we" or "our team" instead of naming '
                "Sentinel Grove a second time"
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
