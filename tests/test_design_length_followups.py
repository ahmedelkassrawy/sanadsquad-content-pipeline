import pytest
from twitter_text import parse_tweet
from content_pipeline.config import Settings, load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.validation import length_feedback, validate
from test_day3_delivery import DeliveryModels, one_slot
from test_pipeline import ROOT, OfflineModels, plan_pair, run, search_factory
from content_pipeline.pipeline import run_pipeline


def check(texts, limit=280):
    pack = ResearchPack(
        slot_id="a",
        topic_id="KB-01",
        topic_name="t",
        sources=[SourceRecord(source_id="KB-01", kind="kb", title="t", evidence_text="evidence")],
    )
    draft = Draft(slot_id="a", tweets=[Tweet(text=text, source_ids=["KB-01"]) for text in texts])
    rules = load_brand(ROOT / "inputs/brand-voice.md")[0]
    return validate(draft, pack, load_type(ROOT, "tech-thread"), rules, limit)


def test_numbered_thread_reports_only_the_overlong_tweet_measured_with_prefix():
    result = check(["short"] * 2 + ["x" * 278] + ["short"] * 2)
    assert result.violations == ["tweet_3:invalid_text", "tweet_3:length"]
    weighted = parse_tweet("3/5 " + "x" * 278).weightedLength
    assert weighted == 282 and parse_tweet("x" * 278).weightedLength == 278
    assert length_feedback(result) == [
        f"tweet_3:weighted_length:282/280:shorten_by_at_least:2:words:2:max_words:37"
    ]


def test_lowered_limit_never_asks_for_zero_words():
    result = check(["x" * 20] * 5, limit=7)
    notes = length_feedback(result, 7)
    assert len(notes) == 5 and all(note.endswith(":max_words:1") for note in notes)


@pytest.mark.asyncio
async def test_dropped_lane_reasons_exclude_writer_only_notes_but_retry_feedback_keeps_them(
    tmp_path,
):
    class OverlengthModels(OfflineModels):
        def factory(self, name, slot_id, context):
            client = super().factory(name, slot_id, context)
            original_call = client.call

            async def call(
                schema, messages, request_slot, node, attempt, max_tokens, output_validator=None
            ):
                result = await original_call(
                    schema, messages, request_slot, node, attempt, max_tokens, output_validator
                )
                if schema.__name__ == "Draft":
                    result.tweets[0].text = "x" * 160
                return result

            client.call = call
            return client

    models = OverlengthModels()
    summary = await run(tmp_path, models, [plan_pair()[0]], settings=Settings(max_tweet_chars=140))

    slot = summary.slots["a"]
    assert slot.outcome.value == "dropped_quality" and slot.attempts == 2
    assert "tweet_1:length" in slot.reasons
    requests = [call[3] for call in models.calls if call[2] == "Draft" and call[1] == "a"]
    assert len(requests) == 2
    assert any(item.startswith("tweet_1:weighted_length:") for item in requests[1]["feedback"])
    assert not any(":weighted_length:" in reason for reason in slot.reasons)


@pytest.mark.asyncio
async def test_plain_value_error_in_advisor_is_failed_api_and_still_ships(tmp_path, monkeypatch):
    def broken(request, output):
        raise ValueError("x")

    monkeypatch.setattr("content_pipeline.pipeline.advice_patch", broken)
    summary = await run_pipeline(
        ROOT,
        one_slot(),
        tmp_path / "run",
        model_factory=DeliveryModels().factory,
        search_factory=search_factory,
    )

    assert summary.shipped == 1
    assert summary.advisor == "failed" and summary.advisor_reason == "failed_api"
