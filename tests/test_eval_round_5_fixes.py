"""Regression tests for deterministic quality fixes from the fifth evaluation."""

import json

from pathlib import Path

import pytest

from content_pipeline.config import Settings, load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet, ValidationResult
from content_pipeline.run_log import render_run_log
from content_pipeline.validation import (
    check_repeated_credits,
    check_section_labels,
    check_stat_claims,
    claim_feedback,
    colon_feedback,
    credit_feedback,
    is_writer_note,
    label_feedback,
    length_feedback,
    link_feedback,
    stat_feedback,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def stat_pack():
    return ResearchPack(
        slot_id="marketing_2",
        topic_id="ANGLE-credibility",
        topic_name="credibility",
        sources=[
            SourceRecord(
                source_id=source_id,
                kind="stat",
                title="Sentinel Grove",
                evidence_text=RULES.approved_stats[source_id],
            )
            for source_id in ("STAT-01", "STAT-02", "STAT-03", "STAT-05")
        ],
    )


def owasp_pack():
    return ResearchPack(
        slot_id="thread_1",
        topic_id="KB-18",
        topic_name="Price oracle manipulation",
        sources=[
            SourceRecord(
                source_id="KB-18",
                kind="kb",
                title="Price oracle manipulation",
                url="https://scs.owasp.org/sctop10/SC03-PriceOracleManipulation/",
                evidence_text="OWASP lists price oracle risks and recommends mitigations.",
            )
        ],
    )


def result_with(*violations, text="x"):
    draft = Draft(slot_id="slot", tweets=[Tweet(text=text)])

    return ValidationResult(
        original=draft,
        normalized=draft,
        fixes=[],
        violations=list(violations),
        accepted=False,
    )


# Fix 1: source-derived credits.


@pytest.mark.parametrize(
    "second",
    [
        "OWASP also recommends y.",
        "OWASP calls this y.",
        "OWASP notes y.",
        "OWASP flags y.",
        "OWASP explains y.",
        "OWASP catalogs y.",
        "OWASP ranks y.",
        "OWASP Smart Contract Top 10 lists y.",
        "OWASP SC03 says y.",
        "In OWASP's words, y.",
        "From OWASP: y.",
    ],
)
def test_a_pack_source_named_in_two_tweets_is_a_repeated_credit(second):
    assert check_repeated_credits(["OWASP lists x.", second], owasp_pack()) == [
        "repeated_credit:owasp"
    ]


def test_a_pack_source_may_be_named_in_one_tweet_only():
    assert (
        check_repeated_credits(["OWASP lists x.", "State the mitigation directly."], owasp_pack())
        == []
    )


def test_a_name_absent_from_the_pack_is_not_treated_as_a_credit():
    texts = ["Per Uniswap pool, the price moves.", "Per Uniswap pool, liquidity matters."]

    assert check_repeated_credits(texts, owasp_pack()) == []


# Fix 2: approved stat binding and distortion.


@pytest.mark.parametrize(
    "text",
    [
        "12 audits per auditor.",
        "Over 12 audits.",
        "More than 12 smart contract audits.",
        "All 12 audits found critical bugs.",
    ],
)
def test_a_qualified_approved_count_is_flagged(text):
    assert check_stat_claims(text, stat_pack())


@pytest.mark.parametrize(
    "text",
    [
        "The vault has 3 functions.",
        "Check these 3 things.",
        "Step 3 of the audit.",
        "Three steps prevent reentrancy.",
        "Solidity 0.8.12 checks overflow.",
        "12 completed smart contract security audits.",
    ],
)
def test_ordinary_technical_counts_and_an_approved_audit_count_pass(text):
    assert check_stat_claims(text, stat_pack()) == []


def test_cross_sentence_stat_relationships_stay_with_the_reviewer():
    text = "Sentinel Grove completed 12 smart contract audits. All 3 auditors worked on each one."

    assert check_stat_claims(text, stat_pack()) == []


# Fix 3: marketing fact stacking.


def test_marketing_posts_limit_approved_facts_and_brand_mentions():
    text = (
        "Sentinel Grove has completed 12 smart contract audits. "
        "Our team has 3 auditors. Teams can request a review of their smart contract code "
        "from Sentinel Grove."
    )
    draft = Draft(
        slot_id="marketing_2",
        tweets=[Tweet(text=text, source_ids=[source.source_id for source in stat_pack().sources])],
    )
    result = validate(draft, stat_pack(), load_type(ROOT, "marketing-post"), RULES)

    assert "approved_stat_limit:3/2" in result.violations
    assert "brand_mention_limit:2/1" in result.violations


def test_other_post_types_do_not_enable_marketing_fact_limits():
    spec = load_type(ROOT, "tech-thread")

    assert spec.max_approved_stats is None
    assert spec.max_brand_mentions is None


def test_the_credibility_brief_requires_one_fact_then_value_then_invitation():
    text = (ROOT / "post_types/marketing-post/writer.md").read_text(encoding="utf-8").casefold()

    assert "open with one concrete firm fact" in text
    assert "never list facts back to back" in text


# Fix 4: repeated points and examples inside a thread.


@pytest.mark.parametrize(
    "tweets",
    [
        [
            "External calls can reenter before state changes take effect.",
            "The root cause is state updates happening after external calls. For example, a withdrawal function that sends Ether before clearing the balance allows an attacker to call back and drain funds again.",
            "In the classic example, a contract holds Ether for a user and lets them withdraw. If the withdrawal sends Ether before reducing the stored balance, a malicious recipient can call back and withdraw the same amount repeatedly.",
            "Update storage before making the external call.",
            "Check every callable path that shares the state.",
        ],
        [
            "A lending protocol checks borrow capacity against the requested loan amount, not the user's collateral. The code runs as written, but the loan exceeds the collateral.",
            "This is a business logic error because the code enforces no economic invariant.",
            "The root cause is missing invariants. The protocol assumes borrow capacity depends only on the requested amount, but in reality, it must depend on the collateral held. Without this check, the system violates its core rule: loans cannot exceed collateral.",
            "Compute borrow capacity from the user's collateral balance.",
            "Test the accounting invariant across every path.",
        ],
    ],
)
def test_a_thread_that_repeats_an_example_or_point_is_rejected(tweets):
    pack = ResearchPack(
        slot_id="thread_1",
        topic_id="KB-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-01",
                kind="kb",
                title="Reentrancy",
                evidence_text=" ".join(tweets),
            )
        ],
    )
    draft = Draft(
        slot_id="thread_1",
        tweets=[Tweet(text=text, source_ids=["KB-01"]) for text in tweets],
    )
    result = validate(draft, pack, load_type(ROOT, "tech-thread"), RULES)

    assert any(":repeats_tweet:" in violation for violation in result.violations)


# Fix 5: outline labels without a brittle finite-verb list.


@pytest.mark.parametrize(
    "text",
    [
        "Mitigation #2: update storage first.",
        "TL;DR: update storage first.",
        "Why this is dangerous: stale state stays visible.",
        "Code pattern: `balances[msg.sender] = 0;`",
    ],
)
def test_more_outline_label_shapes_are_flagged(text):
    assert check_section_labels([text])


@pytest.mark.parametrize(
    "text",
    [
        "The vault mints shares: first deposit sets price.",
        "Callbacks reenter: state is stale.",
        "Price reads spot reserves: one swap moves it.",
        "An attacker deposits 1 wei: shares become 1.",
    ],
)
def test_a_finite_verb_before_a_colon_makes_a_sentence_not_a_label(text):
    assert check_section_labels([text]) == []


# Fix 6: one structural writer-note marker.


def test_every_writer_only_note_uses_the_writer_note_prefix():
    notes = []
    notes += colon_feedback(result_with("tweet_1:dangling_colon"))
    notes += claim_feedback(result_with("tweet_1:unsupported_claim_word:precise"))
    notes += link_feedback(result_with("tweet_1:link_domain:target.call"))
    notes += stat_feedback(result_with("tweet_1:stat_misbound:12 auditor"))
    notes += credit_feedback(result_with("repeated_credit:owasp"))
    notes += label_feedback(result_with("tweet_1:section_label:code pattern"))
    notes += length_feedback(result_with("tweet_1:length", text="x" * 281))

    assert len(notes) == 7
    assert all(note.startswith("writer_note:") for note in notes)
    assert all(is_writer_note(note) for note in notes)


def test_writer_note_detection_uses_only_the_prefix():
    assert is_writer_note("writer_note:tweet_1:anything")
    assert not is_writer_note("tweet_1:weighted_length:300/280")
    assert not is_writer_note("reason:name this source once")
    assert not is_writer_note("reason:delete the clause containing precise")


def test_run_log_hides_writer_notes_from_terminal_reasons(tmp_path):
    summary = {
        "slots": {
            "marketing_1": {
                "outcome": "dropped_quality",
                "attempts": 2,
                "reasons": ["writer_note:delete one fact", "approved_stat_limit:3/2"],
            }
        },
        "shipped": 0,
        "confirmed_tokens": 0,
        "uncertain_tokens": 0,
        "confirmed_cost_usd": 0,
        "uncertain_cost_usd": 0,
        "unpriced": False,
        "advisor": "not_enabled",
    }

    log = render_run_log(tmp_path, summary)

    assert "writer_note:" not in log
    assert "approved_stat_limit:3/2" in log


# Fix 7: post-Merge weak-randomness guidance.


def test_weak_randomness_entry_uses_current_ethereum_terms():
    data = json.loads((ROOT / "inputs/kb.json").read_text(encoding="utf-8"))
    entry = next(item for item in data["entries"] if item["id"] == "KB-10")
    text = (entry["description"] + " " + entry["root_cause"]).casefold()

    assert "since the merge" in text
    assert "validators" in text or "proposers" in text
    assert "block.prevrandao" in text
    assert "not safe randomness" in text
    assert "no longer actively maintained" in text


# Fix 8: token headroom.


def test_default_run_token_budget_is_four_hundred_thousand():
    assert Settings().run_token_budget == 400_000
    assert "RUN_TOKEN_BUDGET=400000" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_documentation_no_longer_names_the_old_default_budget():
    paths = [ROOT / "README.md", ROOT / "docs/providers-and-cost.md"]

    assert all("250,000" not in path.read_text(encoding="utf-8") for path in paths)
