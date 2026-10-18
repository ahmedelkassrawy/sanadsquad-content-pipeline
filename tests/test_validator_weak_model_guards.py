"""Code checks that keep weaker writer models from shipping filler or burning their retry."""

from pathlib import Path
from twitter_text import parse_tweet
from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.validation import (
    check_claim_words,
    claim_feedback,
    is_writer_note,
    validate,
)
from content_pipeline.x_format import split_at_sentence

ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def pack(slot_id, evidence, source_id="KB-01"):
    return ResearchPack(
        slot_id=slot_id,
        topic_id=source_id,
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id=source_id,
                kind=source_id.split("-")[0].lower(),
                title="Reentrancy",
                evidence_text=evidence,
            )
        ],
    )


def marketing(text, evidence="Sentinel Grove has completed 12 smart contract audits."):
    research = pack("marketing_1", evidence, "STAT-01")
    draft = Draft(slot_id="marketing_1", tweets=[Tweet(text=text, source_ids=["STAT-01"])])

    return validate(draft, research, load_type(ROOT, "marketing-post"), RULES)


def test_marketing_type_loads_its_claim_words():
    words = load_type(ROOT, "marketing-post").claim_words

    assert {"thorough", "specialized", "expertise", "rigorous", "precise"} <= set(words)
    # Security advice uses these legitimately, so the reviewer judges them, not code.
    assert not {"ensure", "prevent", "catch", "protect", "critical"} & set(words)
    assert load_type(ROOT, "tech-thread").claim_words == ["twice", "thrice"]


def test_filler_quality_claims_are_rejected_before_review():
    # A real free-model draft that the reviewer dropped twice.
    result = marketing(
        "Smart contract security starts with a thorough review. With 12 completed audits and a "
        "team of 3 specialized auditors, Sentinel Grove provides the expertise needed."
    )

    assert result.accepted is False
    assert {
        "tweet_1:unsupported_claim_word:thorough",
        "tweet_1:unsupported_claim_word:specialized",
        "tweet_1:unsupported_claim_word:expertise",
    } <= set(result.violations)


def test_plain_firm_facts_pass_the_claim_word_check():
    result = marketing(
        "Who reads your smart contract code before it ships? Sentinel Grove has completed 12 "
        "smart contract audits. Request a review of your code."
    )

    assert not [v for v in result.violations if "claim_word" in v]


def test_claim_words_are_allowed_when_the_evidence_uses_them():
    research = pack(
        "thread_1", "Updating state first prevents the callback from withdrawing again."
    )

    assert (
        check_claim_words("This order prevents a second withdrawal.", research, ["prevents"]) == []
    )
    assert check_claim_words("Audits prevent losses.", research, ["prevent"]) == [
        "unsupported_claim_word:prevent"
    ]


def test_claim_words_match_whole_words_and_phrases_after_folding():
    research = pack("marketing_1", "Sentinel Grove has 3 auditors.")

    assert check_claim_words("A catchy line.", research, ["catch"]) == []
    assert check_claim_words("Get ＴＨＯＲＯＵＧＨ checks.", research, ["thorough"]) == [
        "unsupported_claim_word:thorough"
    ]
    assert check_claim_words("Peace of mind for teams.", research, ["peace of mind"]) == [
        "unsupported_claim_word:peace of mind"
    ]


def test_claim_feedback_says_delete_and_stays_out_of_drop_reasons():
    result = marketing("Our rigorous review of 12 smart contract audits.")
    notes = claim_feedback(result)

    assert notes == [
        'tweet_1:unsupported_claim_word:rigorous:delete the clause containing "rigorous"; '
        "do not replace it with another general claim; a shorter post is fine"
    ]
    assert is_writer_note(notes[0])
    assert not is_writer_note("tweet_1:unsupported_claim_word:rigorous")


def thread(texts):
    research = pack("thread_1", "External calls can reenter before state is updated.")
    draft = Draft(slot_id="thread_1", tweets=[Tweet(text=t, source_ids=["KB-01"]) for t in texts])

    return validate(draft, research, load_type(ROOT, "tech-thread"), RULES)


SHORT = "External calls can reenter before state is updated."
LONG_HALF = (
    "External calls can reenter before state is updated, so the callback sees old state " * 2
)


def test_an_overlong_tweet_is_split_at_a_sentence_instead_of_rejected():
    long_tweet = LONG_HALF.strip() + ". " + LONG_HALF.strip() + "."

    result = thread([SHORT, long_tweet, SHORT, SHORT, SHORT])

    assert "tweet_2:split_for_length" in result.fixes
    assert len(result.normalized.tweets) == 6
    assert not [v for v in result.violations if v.endswith(":length")]
    assert all(parse_tweet(t.text).weightedLength <= 280 for t in result.normalized.tweets)
    # Both halves keep the original citations and the thread is renumbered by code.
    assert result.normalized.tweets[2].source_ids == ["KB-01"]
    assert result.normalized.tweets[5].text.startswith("6/6 ")


def test_no_split_when_the_thread_is_already_at_its_maximum():
    long_tweet = LONG_HALF.strip() + ". " + LONG_HALF.strip() + "."

    result = thread([long_tweet] + [SHORT] * 9)

    assert "tweet_1:length" in result.violations
    assert not [f for f in result.fixes if f.endswith("split_for_length")]


def test_split_never_breaks_inside_code_or_without_a_sentence_boundary():
    code = "Clear it first with `balances[msg.sender] = 0. Then call.` " + "x" * 20

    assert split_at_sentence(code, 280) is None
    assert split_at_sentence("word " * 70, 280) is None


def test_markdown_emphasis_is_removed_outside_code_and_logged():
    result = thread(["A withdrawal sends Ether *before* clearing the **balance**."] + [SHORT] * 4)

    assert result.normalized.tweets[0].text == (
        "1/5 A withdrawal sends Ether before clearing the balance."
    )
    assert "tweet_1:markdown" in result.fixes


def test_markdown_emphasis_keeps_code_and_arithmetic():
    from content_pipeline.x_format import strip_markdown

    assert strip_markdown("Use `a * b` and `*ptr*`.") == "Use `a * b` and `*ptr*`."
    assert strip_markdown("2 * 3 * 4 and a*b*c") == "2 * 3 * 4 and a*b*c"


def test_link_feedback_explains_how_to_rewrite_code_that_x_would_link():
    from content_pipeline.validation import link_feedback

    result = thread(["Then it calls `target.call` with the amount."] + [SHORT] * 4)
    notes = link_feedback(result)

    assert "tweet_1:link_domain:target.call" in result.violations
    assert len(notes) == 1 and "a low-level call to target" in notes[0]
    assert is_writer_note(notes[0])
    assert not is_writer_note("tweet_1:link_domain:target.call")


def test_each_angle_pack_holds_only_its_configured_stats():
    from content_pipeline.models import ResolvedSlot
    from content_pipeline.post_types import PostTypes
    from content_pipeline.research import KnowledgeBase

    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    packs = {}

    for angle in types.angles("marketing-post"):
        slot = ResolvedSlot(slot_id="marketing_1", type="marketing-post", topic_id=angle.id)
        stats = {s.source_id for s in types.assigned_pack(slot, kb).sources if s.kind == "stat"}
        packs[angle.name] = stats

        assert stats == set(angle.stat_ids), angle.id

    # Service and credibility differ, so the two posts cannot be built from the same facts.
    assert packs["service"] != packs["credibility"]
    assert "STAT-04" in packs["service"]


def test_claim_words_inside_code_spans_are_not_claims():
    research = pack("marketing_1", "Sentinel Grove has 3 auditors.")

    assert check_claim_words("Wrap it in `try/expert` first.", research, ["expert"]) == []
    assert check_claim_words("Our expert team.", research, ["expert"]) == [
        "unsupported_claim_word:expert"
    ]


def test_untagged_code_fence_keeps_its_first_word():
    from content_pipeline.x_format import strip_markdown

    assert strip_markdown("Guard it: ```require(msg.sender == owner);``` first.") == (
        "Guard it: `require(msg.sender == owner);` first."
    )
    assert strip_markdown("Set ```uint256 x = 1;``` once.") == "Set `uint256 x = 1;` once."


def test_no_split_when_widening_the_numbering_would_push_another_tweet_over():
    # Tweet 1 fits exactly with "1/9 " but would be 281 with "1/10 ".
    filler = "word " * 60
    first = filler[: 280 - len("1/9 ")].rstrip()
    first = first + "x" * (280 - len("1/9 ") - len(first))
    long_tweet = LONG_HALF.strip() + ". " + LONG_HALF.strip() + "."

    result = thread([first] + [SHORT] * 7 + [long_tweet])

    assert parse_tweet("1/9 " + first).weightedLength == 280
    assert not [f for f in result.fixes if f.endswith("split_for_length")]
    assert "tweet_9:length" in result.violations
    assert "tweet_1:length" not in result.violations


def test_code_fences_become_inline_code():
    from content_pipeline.x_format import strip_markdown

    fenced = "Restrict it: ```solidity require(msg.sender == owner); ``` today."

    assert strip_markdown(fenced) == "Restrict it: `require(msg.sender == owner);` today."


def test_a_stray_backtick_does_not_hide_claim_words():
    research = pack("marketing_1", "Sentinel Grove has 3 auditors.")

    assert check_claim_words("It`s an expert team.", research, ["expert"]) == [
        "unsupported_claim_word:expert"
    ]


def test_a_tag_word_used_as_code_is_kept():
    from content_pipeline.x_format import strip_markdown

    assert strip_markdown("Do ```text = load()``` now.") == "Do `text = load()` now."
    assert strip_markdown("Do ```json.parse(x)``` now.") == "Do `json.parse(x)` now."
