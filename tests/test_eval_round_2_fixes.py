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


def test_week_selection_records_angle_source_and_warns_on_a_forced_repeat():
    from types import SimpleNamespace
    from content_pipeline.contracts import SlotState
    from content_pipeline.pipeline import Pipeline

    types = PostTypes(ROOT)
    candidates = next(a for a in types.angles("marketing-post") if a.pick_one).source_ids
    threads = [
        SlotState(slot=ResolvedSlot(slot_id=f"t{i}", type="tech-thread", topic_id=kb_id))
        for i, kb_id in enumerate(candidates)
    ]
    education = SlotState(slot=education_slot())
    host = SimpleNamespace(types=types)

    warnings = Pipeline._choose_angle_sources(host, threads[:1] + [education])

    assert education.slot.angle_source == candidates[1] and warnings == []

    education = SlotState(slot=education_slot())
    warnings = Pipeline._choose_angle_sources(host, threads + [education])

    assert education.slot.angle_source == candidates[0]
    assert warnings == [f"duplicate_topic:marketing_3:{candidates[0]}"]


def test_redaction_covers_key_values_of_any_shape():
    from content_pipeline.model_client import ModelFailure

    error = ValueError(
        "GET https://api.example.com/v1?api_key=tvly1234abcd5678 failed: "
        "secret=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY Authorization: Bearer ab.cd.ef"
    )
    described = describe_error(error)

    for leaked in ["tvly1234abcd5678", "wJalrXUtnFEMI", "ab.cd.ef"]:
        assert leaked not in described

    # Short secrets in quoted bodies or after a plain space.
    for shape in [
        'body {"api_key": "QQshort123"}',
        "{'password': 'QQshort123'}",
        'token="QQshort123"',
        "api key QQshort123 is invalid",
    ]:
        assert "QQshort123" not in describe_error(RuntimeError(shape)), shape
    assert describe_error(ModelFailure("auth")) == "ModelFailure: auth"
    assert describe_error(ModelFailure("failed_output", "slot_id_mismatch")) == (
        "ModelFailure: failed_output (slot_id_mismatch)"
    )


def test_numbered_steps_and_closing_checklists_may_share_an_opening():
    assert check_repeated_openings(["Step 1: a.", "Step 2: b.", "Step 3: c."]) == []
    assert check_repeated_openings(["Is a set?", "Is b set?", "Is c set?"]) == []


def test_a_two_colour_palette_still_draws_a_card(tmp_path):
    spec = ImageSpec(
        concept="c", scene="s", palette=["#14213d", "#ffffff"], overlay_text="Order", alt_text="a"
    )

    render_card(spec, tmp_path / "card.png")

    with Image.open(tmp_path / "card.png") as image:
        assert image.size == (1200, 675)


def test_an_unknown_angle_stat_id_is_refused(tmp_path):
    import shutil
    import pytest

    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    path = root / "post_types/marketing-post/angles.yaml"
    path.write_text(path.read_text().replace("STAT-04", "STAT-99"))

    with pytest.raises(ValueError, match="unknown approved stat"):
        PostTypes(root).angles("marketing-post")


async def test_the_style_reaches_renderers_that_accept_it_only(tmp_path):
    from content_pipeline.image_backends import render_image
    from content_pipeline.images import CardStyle

    calls = []

    def plain(spec, path):
        calls.append(("plain", None))

    def styled(spec, path, style=None):
        calls.append(("styled", style))

    async def count(key):
        pass

    spec = ImageSpec(concept="c", scene="s", palette=RULES.palette, overlay_text="o", alt_text="a")
    style = CardStyle(template="technical", brand_name="B")

    for renderer in (plain, styled):
        await render_image(
            spec, tmp_path / "x.png", ["pillow_card"], tmp_path, renderer, count, style
        )

    assert calls == [("plain", None), ("styled", style)]
