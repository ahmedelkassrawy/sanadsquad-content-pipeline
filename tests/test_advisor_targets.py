"""Advisor instruction-only targets, evidence-id edits and recommendations-only output."""

import json
from pathlib import Path
from types import SimpleNamespace
from content_pipeline.pipeline import run_pipeline
from test_pipeline_resume_and_advisor_delivery import DeliveryModels, one_slot
from test_pipeline_end_to_end import search_factory

ROOT = Path(__file__).resolve().parents[1]
PHRASE = "must be exactly one of the keys of the request's instructions map"
PHRASE2 = "copied character for character from that file text"
GUARDED = ["post_types/tech-thread/writer.md", "inputs/brand-voice.md", "agents/advisor/system.md"]


class TargetModels(DeliveryModels):
    def __init__(self, edits_mode):
        super().__init__()
        self.edits_mode = edits_mode
        self.advisor_messages = []

    def factory(self, name, slot_id, context):
        client = super().factory(name, slot_id, context)
        inner = client.transport

        async def transport(messages, schema, max_tokens):
            if schema.__name__ != "AdvisorOutput":
                return await inner(messages, schema, max_tokens)
            self.advisor_messages.append(messages)
            parsed = {
                "slot_id": "_run",
                "attempt": 1,
                "recommendations": ["Check the encoding of apostrophes in shipped posts."],
                "edits": [],
            }
            if self.edits_mode == "record_id":
                parsed["edits"] = [
                    {
                        "path": "a/validator/1/1",
                        "source_sha256": "0" * 64,
                        "before": "1/5 State before calls.",
                        "after": "1/5 State before calls!",
                    }
                ]
            return {
                "raw": SimpleNamespace(
                    content=json.dumps(parsed),
                    usage_metadata={"input_tokens": 20, "output_tokens": 30},
                    response_metadata={"finish_reason": "stop"},
                ),
                "parsed": parsed,
                "parsing_error": None,
            }

        client.transport = transport
        return client


def snapshot():
    return {name: (ROOT / name).read_bytes() for name in GUARDED}


async def test_advisor_system_message_states_instruction_only_targets(tmp_path):
    models = TargetModels("empty")
    await run_pipeline(
        ROOT,
        one_slot(),
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert models.advisor_messages
    sent = "\n".join(
        str(message["content"])
        for message in models.advisor_messages[0]
        if message.get("role", message.get("type")) in ("system",)
    )
    assert PHRASE in sent
    assert PHRASE2 in sent


async def test_evidence_record_id_edit_target_still_fails_closed(tmp_path):
    before = snapshot()
    models = TargetModels("record_id")
    output = tmp_path / "run"
    summary = await run_pipeline(
        ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
    )
    assert summary.shipped == 1
    assert summary.advisor == "failed" and summary.advisor_reason == "failed_output"
    assert (output / "advice.patch").read_text() == ""
    assert snapshot() == before


async def test_recommendations_only_output_completes_with_empty_patch(tmp_path):
    models = TargetModels("empty")
    output = tmp_path / "run"
    summary = await run_pipeline(
        ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
    )
    assert summary.shipped == 1 and summary.advisor == "completed"
    assert (
        "Check the encoding of apostrophes in shipped posts." in (output / "advice.md").read_text()
    )
    assert (output / "advice.patch").read_text() == ""
