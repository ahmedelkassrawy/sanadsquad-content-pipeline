"""Regression tests for the third evaluation: approved stats stay separate facts, research
failures say why, and marketing angles no longer share a firm description."""

from pathlib import Path
from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, ResolvedSlot, SourceRecord, Tweet
from content_pipeline.post_types import PostTypes
from content_pipeline.research import KnowledgeBase
from content_pipeline.validation import (
    check_stat_claims,
    is_writer_note,
    stat_feedback,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def stat_pack(ids=("STAT-01", "STAT-02")):
    return ResearchPack(
        slot_id="marketing_2",
        topic_id="ANGLE-web3-security",
        topic_name="web3-security",
        sources=[
            SourceRecord(
                source_id=id,
                kind="stat",
                title="Example Firm",
                evidence_text={
                    "STAT-01": "Example Firm completed 4 assessments.",
                    "STAT-02": "Example Firm completed 2 reviews.",
                }[id],
            )
            for id in ids
        ],
    )


def test_two_stats_joined_in_one_sentence_are_rejected():
    # The sentence the evaluator flagged in the round-2 sample.
    text = "Example Firm completed 4 assessments and 2 reviews."

    assert check_stat_claims(text, stat_pack()) == ["joined_stats:STAT-01+STAT-02"]


def test_a_stat_number_moved_to_another_noun_is_rejected():
    assert check_stat_claims("Example Firm has 4 reviewers.", stat_pack()) == [
        "stat_misbound:4 reviewers"
    ]


def test_each_stat_in_its_own_sentence_passes():
    text = "Example Firm completed 4 assessments. Example Firm completed 2 reviews."

    assert check_stat_claims(text, stat_pack()) == []


def test_packs_without_stats_and_numbers_in_code_are_not_checked():
    thread = ResearchPack(
        slot_id="thread_1",
        topic_id="SS-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="SS-01", kind="kb", title="Reentrancy", evidence_text="12 calls, 3 steps."
            )
        ],
    )

    assert check_stat_claims("12 calls and 3 steps in one sentence.", thread) == []
    assert check_stat_claims("Set `x = 12; y = 3;` once.", stat_pack()) == []


def test_joined_stats_fail_validation_with_a_writer_note():
    draft = Draft(
        slot_id="marketing_2",
        tweets=[
            Tweet(
                text="Example Firm completed 4 assessments and 2 reviews.",
                source_ids=["STAT-01", "STAT-02"],
            )
        ],
    )

    result = validate(draft, stat_pack(), load_type(ROOT, "marketing-post"), RULES)
    notes = stat_feedback(result)

    assert "tweet_1:joined_stats:STAT-01+STAT-02" in result.violations
    assert len(notes) == 1 and "its own sentence" in notes[0]
    assert is_writer_note(notes[0])
    assert not is_writer_note("tweet_1:joined_stats:STAT-01+STAT-02")


def test_only_credibility_describes_the_firm():
    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    firm = {}
    offer = {}

    for angle in types.angles("marketing-post"):
        slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id=angle.id)
        ids = {s.source_id for s in types.assigned_pack(slot, kb).sources}
        firm[angle.name] = "STAT-01" in ids
        offer[angle.name] = "STAT-05" in ids

    assert firm == {
        "web3-security": True,
        "web2-pentesting": False,
        "agentic-security-and-red-teaming": False,
        "agentic-payments-x402-vs-mpp": False,
        "vulnerability-class": False,
    }
    assert offer == {
        "web3-security": True,
        "web2-pentesting": True,
        "agentic-security-and-red-teaming": True,
        "agentic-payments-x402-vs-mpp": True,
        "vulnerability-class": False,
    }


def test_an_empty_stat_list_means_the_angle_uses_no_firm_stats():
    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id="ANGLE-vulnerability-class")

    kinds = {s.kind for s in types.assigned_pack(slot, kb).sources}

    assert kinds == {"kb"}


async def test_research_failures_say_why():
    from content_pipeline.model_client import ModelFailure
    from content_pipeline.pipeline import describe_error

    error = ModelFailure("failed_research", "step_limit_reached_without_supporting_evidence")

    assert describe_error(error) == (
        "ModelFailure: failed_research (step_limit_reached_without_supporting_evidence)"
    )


def test_natural_rewordings_of_a_joined_or_misbound_stat_are_caught():
    pack = stat_pack()

    assert check_stat_claims("Example Firm completed 2 reviews and 4 assessments.", pack) == [
        "joined_stats:STAT-01+STAT-02"
    ]
    assert check_stat_claims("Example Firm completed two reviews and 4 assessments.", pack) == [
        "joined_stats:STAT-01+STAT-02"
    ]
    assert check_stat_claims("Example Firm has 4 reviewers.", pack) == ["stat_misbound:4 reviewers"]


def test_reordered_or_linked_stat_numbers_are_not_false_positives():
    pack = stat_pack()

    for text in [
        "4 completed assessments.",
        "A 2-function process.",
        "See https://example.com/4 for details.",
    ]:
        assert check_stat_claims(text, pack) == [], text


def test_twice_in_a_thread_needs_evidence_that_says_twice():
    pack = ResearchPack(
        slot_id="thread_1",
        topic_id="SS-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="SS-01",
                kind="kb",
                title="Reentrancy",
                evidence_text="A callback can withdraw again against the unchanged balance.",
            )
        ],
    )
    texts = [f"Point {n}: the callback can withdraw twice here." for n in "ABCDE"]
    draft = Draft(slot_id="thread_1", tweets=[Tweet(text=t, source_ids=["SS-01"]) for t in texts])

    result = validate(draft, pack, load_type(ROOT, "tech-thread"), RULES)

    assert "tweet_1:unsupported_claim_word:twice" in result.violations


def test_a_possessive_opening_counts_as_the_same_word():
    from content_pipeline.validation import check_repeated_openings

    texts = ["OWASP calls this a flaw.", "OWASP's example shows it.", "OWASP lists areas."]

    assert check_repeated_openings(texts) == ["repeated_opening:owasp"]


def test_a_source_credited_in_two_tweets_is_rejected_with_a_note():
    from content_pipeline.validation import check_repeated_credits, credit_feedback

    texts = [
        "1/5 Price oracle manipulation, listed by OWASP as SC03.",
        "2/5 An oracle is a trust boundary.",
        "3/5 OWASP lists several ways in.",
        "4/5 Aggregate sources and reject outliers.",
        "5/5 Can one pool set the price?",
    ]

    pack = ResearchPack(
        slot_id="thread_1",
        topic_id="SS-01",
        topic_name="x",
        sources=[
            SourceRecord(
                source_id="SS-01",
                kind="kb",
                title="OWASP Smart Contract Top 10",
                url="https://scs.owasp.org/sctop10/SC03-PriceOracleManipulation/",
                evidence_text="SC03 OWASP.",
            )
        ],
    )
    solidity_pack = ResearchPack(
        slot_id="thread_1",
        topic_id="SS-01",
        topic_name="x",
        sources=[
            SourceRecord(
                source_id="SS-01",
                kind="kb",
                title="Solidity documentation",
                url="https://docs.soliditylang.org/en/latest/security-considerations.html",
                evidence_text="Solidity documentation.",
            )
        ],
    )

    assert check_repeated_credits(texts, pack) == ["repeated_credit:owasp"]
    assert check_repeated_credits(texts[:2] + texts[3:], pack) == []
    assert check_repeated_credits(
        ["The Solidity documentation says x.", "Per the Solidity docs, y."], solidity_pack
    ) == ["repeated_credit:solidity"]

    draft = Draft(
        slot_id="thread_1", tweets=[Tweet(text=t[4:], source_ids=["SS-01"]) for t in texts]
    )
    result = validate(draft, pack, load_type(ROOT, "tech-thread"), RULES)
    notes = credit_feedback(result)

    assert "repeated_credit:owasp" in result.violations
    assert len(notes) == 1 and is_writer_note(notes[0])


def test_focus_matches_the_kb_label_not_its_description():
    from content_pipeline.agents import focus_matches

    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    assert focus_matches("oracle manipulation", kb, "SS-03738")
    assert focus_matches("front running", kb, "SS-03813")
    assert focus_matches("DoS", kb, "SS-03928")
    # A description can mention prices without becoming a label match for an unrelated focus.
    assert not focus_matches("Zorvex ledger re-pricing exploit in Cairo 9 vaults", kb, "SS-03926")
    assert focus_matches("x", kb, "SS-03926")


def test_ordinary_capitalized_prose_is_not_a_credit():
    from content_pipeline.validation import check_repeated_credits

    pack = ResearchPack(
        slot_id="thread_1",
        topic_id="SS-18",
        topic_name="oracle",
        sources=[
            SourceRecord(
                source_id="SS-18",
                kind="kb",
                title="OWASP Smart Contract Top 10",
                url="https://scs.owasp.org/",
                evidence_text="OWASP guidance.",
            )
        ],
    )

    assert check_repeated_credits(["External calls can fail.", "External calls again."], pack) == []
    assert (
        check_repeated_credits(
            ["Funds come from Uniswap pools.", "Swaps from Uniswap set the price."], pack
        )
        == []
    )
    assert (
        check_repeated_credits(["Function names collide.", "Function names collide."], pack) == []
    )


def _label_result(last_tweet):
    texts = [
        "Price oracle manipulation lets one pool set the price.",
        "An oracle is a trust boundary.",
        "A single pool can be moved inside one transaction.",
        "Aggregate sources and reject outliers.",
        last_tweet,
    ]
    draft = Draft(slot_id="thread_1", tweets=[Tweet(text=t, source_ids=["SS-01"]) for t in texts])
    pack = ResearchPack(
        slot_id="thread_1",
        topic_id="SS-01",
        topic_name="x",
        sources=[SourceRecord(source_id="SS-01", kind="kb", title="x", evidence_text="oracle.")],
    )

    return validate(draft, pack, load_type(ROOT, "tech-thread"), RULES)


def test_a_tweet_opening_with_an_outline_label_is_rejected_with_a_writer_note():
    from content_pipeline.validation import label_feedback

    result = _label_result("Closing check: does one pool set the price?")
    notes = label_feedback(result)

    assert "tweet_5:section_label:closing check" in result.violations
    assert len(notes) == 1 and is_writer_note(notes[0])
    assert not is_writer_note("tweet_5:section_label:closing check")


def test_an_imperative_lead_in_is_not_a_section_label():
    from content_pipeline.validation import check_section_labels

    assert check_section_labels(["Check each function: does it call out before updating?"]) == []
    assert not any(
        ":section_label:" in v
        for v in _label_result("Check each function: does one pool set the price?").violations
    )


def test_hyphenated_focus_words_join_and_aliases_cover_common_spellings():
    from content_pipeline.agents import focus_matches

    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    assert focus_matches("front-running", kb, "SS-03813")
    assert focus_matches("frontrunning", kb, "SS-03813")
    assert focus_matches("DoS", kb, "SS-03928")
    assert focus_matches("DoS", kb, "SS-03812")
    assert not focus_matches("Zorvex ledger re-pricing exploit in Cairo 9 vaults", kb, "SS-03926")

    for entry in kb.entries:
        assert focus_matches(entry["name"], kb, entry["id"])
