"""The writer's identity fields belong to the pipeline, and a refusal says which check failed."""

from pathlib import Path

import pytest

from content_pipeline.agents import Writer
from content_pipeline.config import load_type
from content_pipeline.contracts import WriteRequest
from content_pipeline.model_client import ModelFailure
from content_pipeline.models import Draft, ResearchPack, Tweet
from content_pipeline.pipeline import _writer_failure_note
from content_pipeline.research import KnowledgeBase

ROOT = Path(__file__).resolve().parents[1]


class ScriptedClient:
    """Returns one fixed draft, whatever the request."""

    def __init__(self, draft):
        self.draft = draft

    async def call(self, schema, messages, slot_id, node, attempt, max_tokens, **kwargs):
        return self.draft


def write_request(attempt):
    source = KnowledgeBase(ROOT / "inputs/kb.json").source("SS-01")

    return WriteRequest(
        slot_id="a",
        attempt=attempt,
        research=ResearchPack(
            slot_id="a", topic_id="SS-01", topic_name=source.title, sources=[source]
        ),
        post_type=load_type(ROOT, "tech-thread"),
        feedback=[],
    )


def draft(slot_id="a", attempt=1):
    return Draft(
        slot_id=slot_id, attempt=attempt, tweets=[Tweet(text="Text.", source_ids=["SS-01"])]
    )


async def test_retry_draft_that_keeps_attempt_one_is_stamped_with_the_real_attempt():
    writer = Writer(ROOT, "tech-thread", ScriptedClient(draft(attempt=1)))

    result = await writer(write_request(attempt=2))

    assert result.attempt == 2


async def test_draft_for_another_slot_is_refused_with_its_reason():
    writer = Writer(ROOT, "tech-thread", ScriptedClient(draft(slot_id="b")))

    with pytest.raises(ModelFailure, match="failed_output") as caught:
        await writer(write_request(attempt=1))

    assert caught.value.detail == "slot_id_mismatch"


def test_writer_failure_note_names_the_check_when_there_is_one():
    assert _writer_failure_note("slot_id_mismatch") == "writer:failed_output:slot_id_mismatch"
    assert _writer_failure_note(None) == "writer:failed_output"
