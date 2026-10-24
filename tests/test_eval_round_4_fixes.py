"""Regression tests for the fourth evaluation: credits with capitalized lead-ins, stats bound to
the noun they count, a stricter focus guard, varied thread styles, general outline labels and an
unambiguous writer-note marker."""

import pytest

from pathlib import Path
from content_pipeline.agents import focus_matches
from content_pipeline.config import load_brand, load_type
from content_pipeline.models import PostType, ResearchPack, ResolvedSlot, SourceRecord
from content_pipeline.post_types import PostTypes
from content_pipeline.research import KnowledgeBase
from content_pipeline.validation import (
    check_repeated_credits,
    check_section_labels,
    check_stat_claims,
    is_writer_note,
    label_feedback,
)
from test_pipeline_end_to_end import OfflineModels, plan_pair, run

ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def stat_pack():
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
            for id in ("STAT-01", "STAT-02")
        ],
    )


def credit_pack(*names):
    identities = {
        "owasp": ("SS-18", "kb", "OWASP Smart Contract Top 10", "https://scs.owasp.org/"),
        "solidity": (
            "WEB-acde",
            "web",
            "Solidity documentation",
            "https://docs.soliditylang.org/",
        ),
        "swc": (
            "WEB-beef",
            "web",
            "SWC registry",
            "https://swcregistry.io/",
        ),
    }

    return ResearchPack(
        slot_id="thread_1",
        topic_id="SS-18",
        topic_name="source",
        sources=[
            SourceRecord(
                source_id=identities[name][0],
                kind=identities[name][1],
                title=identities[name][2],
                url=identities[name][3],
                evidence_text=name,
            )
            for name in names
        ],
    )


# Fix 1: credits.


def test_a_capitalized_lead_in_is_still_a_credit():
    texts = [
        "OWASP says price inputs are trusted.",
        "OWASP names three forms.",
        "Per OWASP, add checks.",
    ]

    assert check_repeated_credits(texts, credit_pack("owasp")) == ["repeated_credit:owasp"]
    assert check_repeated_credits(
        ["OWASP says x.", "As described by OWASP, y."], credit_pack("owasp")
    ) == ["repeated_credit:owasp"]
    assert check_repeated_credits(
        ["Listed by OWASP as SC03.", "Cited by OWASP again."], credit_pack("owasp")
    ) == ["repeated_credit:owasp"]


def test_possessive_and_the_x_says_forms_are_credits():
    assert check_repeated_credits(
        ["OWASP's guidance says x.", "Per OWASP, y."], credit_pack("owasp")
    ) == ["repeated_credit:owasp"]
    assert check_repeated_credits(
        ["The SWC registry says x.", "Per the SWC registry, y."], credit_pack("swc")
    ) == ["repeated_credit:swc"]


def test_capitalized_lead_ins_do_not_make_ordinary_prose_a_credit():
    pack = credit_pack("owasp", "swc")

    assert (
        check_repeated_credits(["Per transaction, one call.", "Per transaction, another."], pack)
        == []
    )
    assert check_repeated_credits(["External calls fail.", "External calls again."], pack) == []
    assert check_repeated_credits(["Funds from Uniswap.", "More from Uniswap pools."], pack) == []
    assert check_repeated_credits(["OWASP says x.", "Different source: Per SWC, y."], pack) == []


# Fix 2: stats bind to the noun they count.


@pytest.mark.parametrize(
    "text",
    [
        "Example Firm's 4 reviewers review every assessment.",
        "Our 4 reviewers have run assessments for years.",
        "4 assessment firms trust us.",
    ],
)
def test_a_stat_number_on_another_head_noun_is_flagged(text):
    violations = check_stat_claims(text, stat_pack())

    assert len(violations) == 1 and violations[0].startswith("stat_misbound:4")


@pytest.mark.parametrize(
    "text",
    [
        "Example Firm completed 4 assessments.",
        "Example Firm completed 2 reviews.",
        "A 2-review team.",
        "A 2-review team checks the code.",
        "4 completed assessments.",
    ],
)
def test_a_stat_number_on_its_own_noun_passes(text):
    assert check_stat_claims(text, stat_pack()) == []


def test_a_stat_joined_across_two_sentences_is_left_to_the_reviewer():
    text = "Example Firm completed 4 assessments. All 2 reviews were documented."

    assert check_stat_claims(text, stat_pack()) == []


# Fix 3: the focus guard.


@pytest.mark.parametrize(
    ("focus", "topic_id"),
    [
        ("proxy storage collision", "SS-03738"),
        ("uninitialized proxy", "SS-03738"),
        ("timestamp dependence", "SS-03813"),
        ("unchecked return value", "SS-03738"),
        ("missing access control", "SS-03928"),
    ],
)
def test_one_shared_generic_word_does_not_match_a_kb_entry(focus, topic_id):
    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    assert not focus_matches(focus, kb, topic_id)


def test_the_right_entry_still_matches_each_focus():
    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    assert focus_matches("price feeds", kb, "SS-03738")
    assert focus_matches("missing signer check", kb, "SS-03859")
    assert focus_matches("access control", kb, "SS-03699")
    assert focus_matches("front-running", kb, "SS-03813")
    assert focus_matches("frontrunning", kb, "SS-03813")
    assert focus_matches("DoS", kb, "SS-03928")
    assert focus_matches("oracle manipulation", kb, "SS-03738")


def test_a_focus_that_matches_no_entry_matches_none():
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    matches = [e["id"] for e in kb.entries if focus_matches("ERC-4626 inflation", kb, e["id"])]

    assert matches == []


# Fix 5: thread styles.


def thread_plan():
    return [ResolvedSlot(slot_id=f"thread_{n}", type="tech-thread") for n in (1, 2, 3)] + [
        ResolvedSlot(slot_id="marketing_1", type="marketing-post"),
    ]


def test_each_thread_gets_the_next_hook_and_closing_style():
    types = PostTypes(ROOT)
    spec = types.spec("tech-thread")
    plan = thread_plan()
    styles = [types.writing_style(slot, plan) for slot in plan[:3]]

    assert len(spec.hook_styles) == 3 and len(spec.closing_styles) == 3
    assert [s.hook for s in styles] == spec.hook_styles
    assert [s.closing for s in styles] == spec.closing_styles


def test_styles_wrap_around_and_count_only_slots_of_the_same_type():
    types = PostTypes(ROOT)
    plan = [ResolvedSlot(slot_id=f"t{n}", type="tech-thread") for n in range(4)]
    plan.insert(1, ResolvedSlot(slot_id="m", type="marketing-post"))
    spec = types.spec("tech-thread")

    assert types.writing_style(plan[4], plan).hook == spec.hook_styles[0]
    assert types.writing_style(plan[2], plan).hook == spec.hook_styles[1]
    assert types.writing_style(plan[1], plan) is None


def test_the_writer_prompt_no_longer_carries_a_copyable_closing():
    text = (ROOT / "post_types/tech-thread/writer.md").read_text(encoding="utf-8")

    assert "does any storage write come after an external call" not in text
    assert "style.hook" in text and "style.closing" in text


def test_blank_styles_are_refused():
    data = load_type(ROOT, "tech-thread").model_dump()

    data["hook_styles"] = ["a plain fact"]

    assert PostType.model_validate(data).hook_styles == ["a plain fact"]

    data["hook_styles"] = ["  "]

    with pytest.raises(ValueError):
        PostType.model_validate(data)


async def test_the_writer_request_carries_the_assigned_style(tmp_path):
    models = OfflineModels()

    await run(tmp_path, models, plan_pair())

    spec = load_type(ROOT, "tech-thread")
    requests = {
        call[1]: call[3] for call in models.calls if call[2] == "Draft" and call[1] in {"a", "b"}
    }

    assert requests["a"]["style"]["hook"] == spec.hook_styles[0]
    assert requests["b"]["style"]["hook"] == spec.hook_styles[1]
    assert requests["b"]["style"]["closing"] == spec.closing_styles[1]


# Fix 6: outline labels.


@pytest.mark.parametrize(
    "text",
    [
        "Two more forms: stale data and thin liquidity.",
        "Root cause in one line: state changes after the call.",
        "A concrete variant: an exposed initializer.",
        "Share math: shares equal assets over supply.",
        "A useful check: who can call this?",
        "Areas to focus on: invariants and fees.",
        "Effect of the offset: donations cost more.",
        "The fix: update state first.",
        "Mechanism: the callback runs early.",
        "Mitigation 1: dead shares.",
        "Key takeaway: order matters.",
        "Closing check:does any write follow a call?",
        "8/9 Closing check: does any write follow a call?",
    ],
)
def test_a_short_lead_in_and_colon_is_an_outline_label(text):
    assert len(check_section_labels([text])) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Check each function: does it call out before updating?",
        "Fix: `balances[msg.sender] = 0;` before the call.",
        "Use the guard: `nonReentrant` blocks the callback.",
        "The fix is checks-effects-interactions: update state, then call.",
        "OWASP lists several forms: spot price manipulation and stale data.",
        "Read https://example.com for the details.",
        "State is zeroed first, then the call runs: the callback sees zero.",
        "Before 0.8.0 arithmetic wraps silently, so any subtraction can underflow past zero here: bad.",
        "A plain sentence with no colon at all.",
    ],
)
def test_ordinary_colon_sentences_are_not_labels(text):
    assert check_section_labels([text]) == []


def test_the_label_id_names_the_label_and_the_note_is_a_writer_note():
    violations = check_section_labels(["Intro text.", "Mitigation 1: dead shares."])

    assert violations == ["tweet_2:section_label:mitigation 1"]


# Fix 7: the writer-note marker.


def test_violations_that_contain_drop_are_not_writer_notes():
    assert not is_writer_note("tweet_1:repeated_opening:dropping")
    assert not is_writer_note("repeated_opening:dropping")
    assert not is_writer_note("tweet_2:unsupported_claim_word:dropped")
    assert not is_writer_note("tweet_2:section_label:drop")


def test_the_label_note_is_still_a_writer_note():
    from content_pipeline.models import ValidationResult, Draft, Tweet

    draft = Draft(slot_id="t", tweets=[Tweet(text="x")])
    result = ValidationResult(
        original=draft,
        normalized=draft,
        fixes=[],
        violations=["tweet_5:section_label:closing check"],
        accepted=False,
    )
    notes = label_feedback(result)

    assert len(notes) == 1 and is_writer_note(notes[0])
    assert not is_writer_note("tweet_5:section_label:closing check")
