"""Regression tests for the round-6 review: the round-5 checks must not lose cases the earlier
checks caught, and must keep the false positives they fixed."""

import json
from pathlib import Path
import pytest
from content_pipeline.config import load_brand
from content_pipeline.models import ResearchPack, SourceRecord
from content_pipeline.validation import (
    check_repeated_credits,
    check_section_labels,
    check_stat_claims,
    is_writer_note,
    post_limit_feedback,
    stat_feedback,
)
from content_pipeline.models import Draft, Tweet, ValidationResult

ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def kb_pack(title, url):
    return ResearchPack(
        slot_id="thread_1",
        topic_id="KB-04",
        topic_name="x",
        sources=[
            SourceRecord(source_id="KB-04", kind="kb", title=title, url=url, evidence_text="x")
        ],
    )


SWC = kb_pack("Unprotected Ether Withdrawal", "https://swcregistry.io/docs/SWC-105")
SOLIDITY = kb_pack("Reentrancy", "https://docs.soliditylang.org/en/latest/security.html")
OWASP = kb_pack("OWASP Smart Contract Top 10", "https://scs.owasp.org/")


# Credits.


def test_solidity_docs_credited_twice_in_an_swc_topic_are_caught():
    texts = ["The Solidity docs say x.", "The Solidity documentation also warns y."]

    assert check_repeated_credits(texts, SWC) == ["repeated_credit:solidity"]


@pytest.mark.parametrize(
    "texts",
    [
        ["Before Solidity 0.8, overflow wrapped silently.", "Since Solidity 0.8.0, it reverts."],
        ["In Solidity 0.8, math is checked.", "In Solidity 0.8 the compiler reverts."],
    ],
)
def test_solidity_as_the_language_is_not_a_credit(texts):
    assert check_repeated_credits(texts, SOLIDITY) == []


@pytest.mark.parametrize(
    "texts,name",
    [
        (["OWASP says x.", "Per OWASP, y."], "owasp"),
        (["The SWC registry says a.", "Per the SWC registry, b."], "swc"),
        (["Trail of Bits recommends a.", "Trail of Bits also warns b."], "trail"),
        (["OWASP lists x.", "In OWASP's words, y."], "owasp"),
    ],
)
def test_publishers_beyond_the_pack_are_credits_too(texts, name):
    assert check_repeated_credits(texts, OWASP) == [f"repeated_credit:{name}"]


# Firm stats.


def stat_pack():
    ids = ("STAT-01", "STAT-02", "STAT-03", "STAT-05")

    return ResearchPack(
        slot_id="marketing_2",
        topic_id="ANGLE-credibility",
        topic_name="credibility",
        sources=[
            SourceRecord(
                source_id=id,
                kind="stat",
                title="Sentinel Grove",
                evidence_text=RULES.approved_stats[id],
            )
            for id in ids
        ],
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sentinel Grove has 12 engineers.",
        "Our 12 security researchers review your code.",
        "Sentinel Grove has 3 reviewers.",
        "Our auditors number 12.",
        "We have completed 12.",
        "Up to 12 smart contract audits completed.",
        "About 12 smart contract audits completed.",
        "Roughly 12 smart contract audits completed.",
        "Sentinel Grove has completed 12+ smart contract audits.",
        "All 12 audits uncovered critical bugs.",
        "Every one of our 12 audits found critical bugs.",
    ],
)
def test_a_stat_number_on_another_firm_noun_or_qualified_is_flagged(text):
    assert check_stat_claims(text, stat_pack()) != []


@pytest.mark.parametrize(
    "text",
    [
        "12 smart contract audits per our records.",
        "Requires Solidity 0.8.12 or later.",
        "The vault has 3 functions.",
        "Step 3 of the audit.",
        "Sentinel Grove has completed 12 smart contract audits.",
    ],
)
def test_attribution_versions_and_technical_counts_pass(text):
    assert check_stat_claims(text, stat_pack()) == []


# Outline labels.


@pytest.mark.parametrize(
    "label",
    [
        "Common pitfalls",
        "Red flags",
        "Attack vectors",
        "Warning signs",
        "Lessons learned",
        "Real-world examples",
        "What changed",
        "One rule matters",
        "Why it happens",
        "Other mitigations",
        "Two things to verify in pre-0.6.0 code",
        "Areas to focus on",
    ],
)
def test_noun_phrase_labels_are_flagged(label):
    assert check_section_labels([f"1/2 {label}: a and b."]) == [
        f"tweet_1:section_label:{label.casefold()}"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Callbacks reenter: state is stale.",
        "Callbacks re-enter: state is stale.",
        "Callbacks run first: the balance is stale.",
        "Check each function: does it update state first?",
        "OWASP names four areas to review: a, b, c.",
        "Reentrancy comes from one simple ordering mistake: the call comes first.",
    ],
)
def test_clauses_and_instructions_before_a_colon_are_not_labels(text):
    assert check_section_labels([f"1/2 {text}"]) == []


# Writer notes read their limits from the violation, not from hard-coded text.


def result_with(violations):
    draft = Draft(slot_id="m", tweets=[Tweet(text="x", source_ids=["STAT-01"])])

    return ValidationResult(
        original=draft, normalized=draft, fixes=[], violations=violations, accepted=False
    )


def test_post_limit_notes_use_the_configured_limits_and_brand():
    notes = post_limit_feedback(
        result_with(["approved_stat_limit:4/3", "brand_mention_limit:3/2"]), "Acme Audits"
    )

    assert "at most 3 approved" in notes[0]
    assert "Acme Audits at most 2" in notes[1]
    assert all(is_writer_note(note) for note in notes)


def test_stat_note_does_not_mention_a_stat_no_pack_carries():
    [note] = stat_feedback(result_with(["tweet_1:stat_misbound:12 engineers"]))

    assert "team size" not in note


def test_kb_09_describes_post_merge_ethereum():
    kb = {e["id"]: e for e in json.loads((ROOT / "inputs/kb.json").read_text("utf-8"))["entries"]}
    text = kb["KB-09"]["description"]

    assert "12-second slots" in text and "historical" in text
