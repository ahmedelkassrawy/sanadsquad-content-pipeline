"""Writer requests carry the effective length limit and the rejected draft as feedback."""

import pytest

from content_pipeline.config import Settings
from content_pipeline.model_client import ModelClient
from test_pipeline_end_to_end import OfflineModels, plan_pair, run


@pytest.mark.asyncio
async def test_public_writer_receives_effective_limit_and_attempt_feedback(tmp_path):
    models = OfflineModels({"a": {"invalid_attempts": [1]}})
    summary = await run(tmp_path, models, [plan_pair()[0]], settings=Settings(max_tweet_chars=140))

    assert summary.slots["a"].attempts == 2
    writer_payloads = [call[3] for call in models.calls if call[2] == "Draft" and call[1] == "a"]
    assert len(writer_payloads) == 2
    assert writer_payloads[0]["max_tweet_chars"] == 140
    assert writer_payloads[0]["previous_draft"] is None
    assert "tweet_1:citation:KB-99" in writer_payloads[1]["feedback"]
    assert writer_payloads[1]["max_tweet_chars"] == 140
    assert writer_payloads[1]["previous_draft"]["tweets"][0]["text"] == "State before calls."
    reviewer_payloads = [
        call[3] for call in models.calls if call[2] == "ReviewAssessment" and call[1] == "a"
    ]
    assert all("previous_draft" not in payload for payload in reviewer_payloads)


@pytest.mark.asyncio
async def test_public_writer_default_limit_and_first_attempt_have_no_prior_draft(tmp_path):
    models = OfflineModels()
    await run(tmp_path, models, [plan_pair()[0]])

    request = next(call[3] for call in models.calls if call[2] == "Draft" and call[1] == "a")
    assert request["max_tweet_chars"] == 280
    assert request["previous_draft"] is None


@pytest.mark.asyncio
async def test_validator_length_feedback_retries_with_the_actual_rejected_draft(
    tmp_path, monkeypatch
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
                if schema.__name__ == "Draft" and attempt == 1:
                    result.tweets[0].text = "x" * 160
                return result

            client.call = call
            return client

    captured = []
    original_call = ModelClient.call

    async def capture(
        self, schema, messages, slot_id, node, attempt, max_tokens, output_validator=None
    ):
        if schema.__name__ == "Draft":
            captured.append(messages)
        return await original_call(
            self, schema, messages, slot_id, node, attempt, max_tokens, output_validator
        )

    monkeypatch.setattr(ModelClient, "call", capture)
    models = OverlengthModels()
    summary = await run(tmp_path, models, [plan_pair()[0]], settings=Settings(max_tweet_chars=140))

    assert summary.slots["a"].attempts == 2
    requests = [call[3] for call in models.calls if call[2] == "Draft" and call[1] == "a"]
    assert "tweet_1:length" in requests[1]["feedback"]
    measured = [
        item
        for item in requests[1]["feedback"]
        if item.startswith("writer_note:tweet_1:weighted_length:")
    ]
    assert len(measured) == 1
    weighted, limit = measured[0].split(":")[3].split("/")
    assert int(weighted) > 160 and limit == "140"
    assert f":shorten_by_at_least:{int(weighted) - 140}:" in measured[0]
    assert measured[0].endswith(":max_words:18")
    assert requests[1]["previous_draft"]["tweets"][0]["text"] == "x" * 160
    assert requests[1]["max_tweet_chars"] == 140
    retry_user = captured[1][1]["content"]
    assert '"max_tweet_chars":140' in retry_user
    assert '"text":"' + ("x" * 160) + '"' in retry_user
    assert '"tweet_1:length"' in retry_user


@pytest.mark.asyncio
async def test_review_feedback_retry_receives_prior_draft_only_in_writer_request(tmp_path):
    models = OfflineModels({"a": {"reject_attempts": [1]}})
    summary = await run(tmp_path, models, [plan_pair()[0]])

    assert summary.slots["a"].attempts == 2
    requests = [call[3] for call in models.calls if call[2] == "Draft" and call[1] == "a"]
    assert requests[0]["previous_draft"] is None
    assert requests[1]["previous_draft"]["tweets"][0]["text"] == "State before calls."
    assert requests[1]["feedback"]
    reviewer_payloads = [
        call[3] for call in models.calls if call[2] == "ReviewAssessment" and call[1] == "a"
    ]
    assert all("previous_draft" not in payload for payload in reviewer_payloads)


@pytest.mark.asyncio
async def test_run_pipeline_captures_real_writer_messages_and_numbered_limit_guidance(
    tmp_path, monkeypatch
):
    captured = []
    original_call = ModelClient.call

    async def capture(
        self, schema, messages, slot_id, node, attempt, max_tokens, output_validator=None
    ):
        if schema.__name__ == "Draft":
            captured.append(messages)
        return await original_call(
            self, schema, messages, slot_id, node, attempt, max_tokens, output_validator
        )

    monkeypatch.setattr(ModelClient, "call", capture)
    models = OfflineModels()
    await run(tmp_path, models, [plan_pair()[0]], settings=Settings(max_tweet_chars=190))

    assert len(captured) == 1
    system_message, user_message = captured[0]
    assert (
        "including the numbering prefix that code adds before validation"
        in system_message["content"]
    )
    assert "minus the prefix's X weighted length" in system_message["content"]
    assert "each URL weighs 23 characters" in system_message["content"]
    assert '"max_tweet_chars":190' in user_message["content"]
    assert '"previous_draft":null' in user_message["content"]
