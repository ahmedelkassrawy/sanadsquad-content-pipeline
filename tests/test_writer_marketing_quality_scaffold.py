"""The marketing writer request includes the angle quality scaffold."""

from pathlib import Path

import pytest

from content_pipeline.agents import Writer
from content_pipeline.config import load_type
from content_pipeline.contracts import WriteRequest
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet


ROOT = Path(__file__).resolve().parents[1]


class CapturingClient:
    def __init__(self):
        self.messages = None

    async def call(
        self, schema, messages, slot_id, node, attempt, max_tokens, output_validator=None
    ):
        self.messages = messages
        return Draft(
            slot_id=slot_id, attempt=attempt, tweets=[Tweet(text="Captured draft", source_ids=[])]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "topic_id,source",
    [
        (
            "ANGLE-service",
            SourceRecord(
                source_id="STAT-03",
                kind="stat",
                title="Sentinel Grove",
                evidence_text="DEMO FIXTURE ONLY: fictional smart contract audit service; teams can request a review.",
            ),
        ),
        (
            "ANGLE-credibility",
            SourceRecord(
                source_id="STAT-01",
                kind="stat",
                title="Sentinel Grove",
                evidence_text="DEMO FIXTURE ONLY: completed 12 fictional audits.",
            ),
        ),
        (
            "ANGLE-education",
            SourceRecord(
                source_id="KB-01",
                kind="kb",
                title="Reentrancy",
                evidence_text="Use checks-effects-interactions to mitigate reentrancy.",
            ),
        ),
    ],
)
async def test_public_marketing_writer_sends_angle_quality_scaffold(topic_id, source):
    client = CapturingClient()
    request = WriteRequest(
        slot_id=topic_id,
        attempt=1,
        research=ResearchPack(
            slot_id=topic_id, topic_id=topic_id, topic_name=topic_id, sources=[source]
        ),
        post_type=load_type(ROOT, "marketing-post"),
        feedback=[],
    )

    await Writer(ROOT, "marketing-post", client)(request)

    assert client.messages[1]["content"] == request.model_dump_json()
    prompt = client.messages[0]["content"]
    assert (
        "finished, reader-facing post around one supported point, not a list of brand facts"
        in prompt
    )
    assert (
        "Open with a specific question or contrast tied to the supplied angle and evidence"
        in prompt
    )
    assert "end with a direct next step that the supplied evidence supports" in prompt
    assert (
        "For an education angle, keep the hook and supported mechanism or mitigation concise"
        in prompt
    )
    assert "A short grounded invitation is enough" not in prompt
    if topic_id == "ANGLE-education":
        assert "leave room within the effective configured limit for a distinct" in prompt
        assert "distinct, evidence-tied reader-engagement question or concrete next step" in prompt
        assert (
            "Merely restating or applying the mitigation is not by itself this engagement CTA"
            in prompt
        )
