"""Regression tests for the second evaluation: varied marketing, honest thread copy, readable
failures and finished image cards."""

from pathlib import Path
from PIL import Image
from content_pipeline.config import load_brand, load_type
from content_pipeline.contracts import VisualRequest
from content_pipeline.images import card_style, render_card
from content_pipeline.models import (
    Draft,
    ImageSpec,
    ResearchPack,
    ResolvedSlot,
    SourceRecord,
    Tweet,
)
from content_pipeline.pipeline import describe_error
from content_pipeline.post_types import PostTypes
from content_pipeline.research import KnowledgeBase
from content_pipeline.validation import check_repeated_openings, validate
from skills import brand_rules

ROOT = Path(__file__).resolve().parents[1]
RULES = load_brand(ROOT / "inputs/brand-voice.md")[0]


def education_slot():
    return ResolvedSlot(slot_id="marketing_3", type="marketing-post", topic_id="ANGLE-education")


def test_education_angle_avoids_a_kb_topic_a_thread_already_covers():
    types = PostTypes(ROOT)

    # thread_1 is about reentrancy (KB-01), so the education post takes the next candidate.
    assert types.choose_angle_source(education_slot(), {"KB-01", "ANGLE-service"}) == "KB-04"
    assert types.choose_angle_source(education_slot(), set()) == "KB-01"


def test_chosen_education_source_is_the_only_kb_record_in_its_pack():
    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    slot = education_slot()
    slot.angle_source = "KB-05"

    kb_ids = [s.source_id for s in types.assigned_pack(slot, kb).sources if s.kind == "kb"]

    assert kb_ids == ["KB-05"]


def test_angle_source_choice_skips_slots_that_are_not_pick_one_angles():
    types = PostTypes(ROOT)
    service = ResolvedSlot(slot_id="marketing_1", type="marketing-post", topic_id="ANGLE-service")
    focused = ResolvedSlot(slot_id="x", type="marketing-post", topic_id="KB-01", focus="reentrancy")
    thread = ResolvedSlot(slot_id="thread_1", type="tech-thread", topic_id="KB-01")

    assert types.choose_angle_source(service, set()) is None
    assert types.choose_angle_source(focused, set()) is None
    assert types.choose_angle_source(thread, set()) is None


def test_three_tweets_opening_with_the_same_word_are_rejected():
    texts = [
        "1/5 A lending check can compute capacity from the wrong value.",
        "2/5 OWASP describes logic flaws as design flaws.",
        "3/5 OWASP lists areas to focus on.",
        "4/5 OWASP also recommends adversarial simulations.",
        "5/5 Can you state each invariant in one sentence?",
    ]

    assert check_repeated_openings(texts) == ["repeated_opening:owasp"]
    assert check_repeated_openings(texts[:3] + texts[4:]) == []
    # Common function words may open several tweets.
    assert check_repeated_openings(["The a.", "The b.", "The c."]) == []


def test_text_that_points_at_the_research_material_is_banned():
    for text in [
        "This stops theft in the article's example.",
        "Tradeoffs from the discussion: virtual shares change behavior.",
        "As the evidence shows, rounding favors the attacker.",
    ]:
        assert any(e.startswith("banned_phrase:") for e in brand_rules.check_brand(text, RULES))

    assert brand_rules.check_brand("Rounding favors the attacker.", RULES) == []


def thread_pack():
    return ResearchPack(
        slot_id="thread_1",
        topic_id="KB-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-01",
                kind="kb",
                title="Reentrancy",
                evidence_text="External calls can reenter before state is updated.",
            )
        ],
    )


def test_the_unicode_minus_sign_is_replaced_like_other_dash_look_alikes():
    texts = [f"Point {n}: call order matters x {chr(0x2212)} 1 here." for n in "ABCDE"]
    draft = Draft(slot_id="thread_1", tweets=[Tweet(text=t, source_ids=["KB-01"]) for t in texts])

    result = validate(draft, thread_pack(), load_type(ROOT, "tech-thread"), RULES)

    assert chr(0x2212) not in " ".join(t.text for t in result.normalized.tweets)
    assert "tweet_1:banned_characters" in result.fixes


def test_error_description_names_the_exception_and_hides_keys():
    error = RuntimeError("401 Unauthorized for key sk-ant-api03-abcdefghijklmnop1234 at host")

    described = describe_error(error)

    assert described.startswith("RuntimeError: 401 Unauthorized")
    assert "sk-ant" not in described and "[redacted]" in described


def visual_request(post_type, tweets):
    return VisualRequest(
        slot_id="thread_1",
        attempt=1,
        draft=Draft(
            slot_id="thread_1", tweets=[Tweet(text=t, source_ids=["KB-01"]) for t in tweets]
        ),
        research=thread_pack(),
        post_type=load_type(ROOT, post_type),
        severity="high",
    )


def test_card_style_takes_code_and_label_from_the_approved_post():
    request = visual_request(
        "tech-thread",
        ["1/2 Order matters.", "2/2 Clear it first: `balances[msg.sender] = 0;` then call."],
    )

    style = card_style(request, "Sentinel Grove")

    assert style.template == "technical"
    assert style.code == "balances[msg.sender] = 0;"
    assert style.label == "Reentrancy"
    assert card_style(visual_request("marketing-post", ["A post."]), "Sentinel Grove").code is None


def test_technical_card_draws_the_code_panel_and_brand_accent(tmp_path):
    spec = ImageSpec(
        concept="c",
        scene="s",
        palette=RULES.palette,
        overlay_text="clear the balance first",
        alt_text="a",
        severity="high",
    )
    request = visual_request("tech-thread", ["Clear it first: `balances[msg.sender] = 0;` now."])
    plain, styled = tmp_path / "plain.png", tmp_path / "styled.png"

    render_card(spec, plain)
    render_card(spec, styled, style=card_style(request, "Sentinel Grove"))

    with Image.open(styled) as image:
        assert image.size == (1200, 675)
        # The accent bar on the left edge uses the brand's second palette colour.
        assert image.getpixel((68, 300)) == (0xFC, 0xA3, 0x11)

    assert plain.read_bytes() != styled.read_bytes()
