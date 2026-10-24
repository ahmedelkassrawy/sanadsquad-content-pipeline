"""The marketing writer receives its assigned format and the compact quality scaffold."""

from pathlib import Path

import pytest

from content_pipeline.agents import Writer
from content_pipeline.config import load_type
from content_pipeline.contracts import WriteRequest
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet


ROOT = Path(__file__).resolve().parents[1]


class CapturingClient:
    async def call(
        self, schema, messages, slot_id, node, attempt, max_tokens, output_validator=None
    ):
        self.messages = messages
        return Draft(slot_id=slot_id, attempt=attempt, tweets=[Tweet(text="Captured draft")])


@pytest.mark.asyncio
async def test_marketing_writer_receives_assigned_format_and_sanadsquad_rules():
    client = CapturingClient()
    spec = load_type(ROOT, "marketing-post")
    format = next(format for format in spec.formats if format.id == "thread")
    request = WriteRequest(
        slot_id="ANGLE-agentic-payments",
        attempt=1,
        research=ResearchPack(
            slot_id="ANGLE-agentic-payments",
            topic_id="ANGLE-agentic-payments",
            topic_name="agentic payments",
            sources=[
                SourceRecord(
                    source_id="STAT-04",
                    kind="stat",
                    title="SanadSquad",
                    evidence_text="SanadSquad provides agentic payment protocol review for x402 and MPP.",
                )
            ],
        ),
        post_type=spec,
        feedback=[],
        format=format,
        cta_allowed=False,
    )

    await Writer(ROOT, "marketing-post", client)(request)

    assert client.messages[1]["content"] == request.model_dump_json()
    assert '"id":"thread"' in client.messages[1]["content"]
    prompt = client.messages[0]["content"]
    assert "whole attack surface, not just the contract" in prompt
    assert "at most one CTA" in prompt
    assert "Sentinel Grove" not in prompt
