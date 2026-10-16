import json
from types import SimpleNamespace

import pytest
from test_pipeline import OfflineModels, run

from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import Pipeline


class VisualModels(OfflineModels):
    """Supply visual responses at the model transport boundary."""

    def __init__(self, overlays, accepted_text=None):
        super().__init__()
        self.overlays = iter(overlays)
        self.visual_requests = []
        self.accepted_text = accepted_text

    def factory(self, name, slot_id, context):
        client = super().factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            if name != "visual":
                result = await original(messages, schema, max_tokens)

                if name == "thread_writer" and self.accepted_text is not None:
                    for tweet in result["parsed"]["tweets"]:
                        tweet["text"] = self.accepted_text
                    result["raw"].content = json.dumps(result["parsed"])

                return result

            self.visual_requests.append(messages)
            payload = json.loads(messages[1]["content"])
            overlay = next(self.overlays)

            if isinstance(overlay, Exception):
                raise overlay

            result = await original(messages, schema, max_tokens)
            result["parsed"]["spec"]["overlay_text"] = overlay
            result["parsed"]["attempt"] = payload["attempt"]
            result["raw"] = SimpleNamespace(
                content=json.dumps(result["parsed"]),
                usage_metadata={"input_tokens": 20, "output_tokens": 30},
                response_metadata={"finish_reason": "stop"},
            )
            return result

        client.transport = transport
        return client


async def visual_run(tmp_path, overlays, accepted_text=None):
    models = VisualModels(overlays, accepted_text)
    await run(tmp_path, models, [ResolvedSlot(slot_id="a", type="tech-thread", topic_id="KB-01")])
    record = json.loads((tmp_path / "run/visuals.json").read_text())[0]
    return models, record


@pytest.mark.parametrize("overlay", ["STATE BEFORE CALLS.", "State   before\ncalls."])
async def test_normalized_excerpt_ships_without_fallback(tmp_path, overlay):
    models, record = await visual_run(tmp_path, [overlay])
    assert record["result"]["fallback_reason"] is None
    assert record["spec"]["overlay_text"] == overlay
    assert len(models.visual_requests) == 1


async def test_ungrounded_overlay_is_repaired_once(tmp_path):
    models, record = await visual_run(tmp_path, ["Invented claim", "State before calls."])
    assert record["result"]["fallback_reason"] is None
    assert record["spec"]["overlay_text"] == "State before calls."
    assert len(models.visual_requests) == 2
    assert json.loads(models.visual_requests[1][1]["content"])["attempt"] == 2
    assert "ungrounded_overlay" in models.visual_requests[1][0]["content"]


async def test_straight_and_curly_quotes_are_grounded(tmp_path):
    _, record = await visual_run(
        tmp_path, ["‘State’ before calls."], accepted_text="'State' before calls."
    )
    assert record["result"]["fallback_reason"] is None


async def test_second_overlay_failure_records_detail(tmp_path):
    models, record = await visual_run(tmp_path, ["Invented claim", "Another invented claim"])
    assert len(models.visual_requests) == 2
    assert record["result"]["fallback_reason"] == "visual_fallback"
    assert record["result"]["fallback_detail"] == ["ungrounded_overlay"]


async def test_agent_exception_records_class_without_message(tmp_path):
    _, record = await visual_run(tmp_path, [RuntimeError("secret-must-not-leak")])
    assert record["result"]["fallback_detail"] == ["ModelFailure"]
    assert "secret-must-not-leak" not in json.dumps(record)


async def test_saved_visual_without_detail_still_validates(tmp_path):
    _, record = await visual_run(tmp_path, ["State before calls."])
    folder = tmp_path / "run"
    record["result"].pop("fallback_detail", None)
    (folder / "visuals.json").write_text(json.dumps([record]), encoding="utf-8")
    audit = json.loads((folder / "drafts.json").read_text())[0]["audit"]
    pack = next(
        row["pack"] for row in json.loads((folder / "research_log.json").read_text()) if row["pack"]
    )
    pipeline = Pipeline(SimpleNamespace(root=folder / "snapshot", run_dir=folder), [])
    result = await pipeline.visual(
        {
            "slot": {"slot_id": "a", "type": "tech-thread", "topic_id": "KB-01"},
            "attempt": 1,
            "pack": pack,
            "audit": audit,
        }
    )
    assert result["outcome"] == "shipped"
    assert result["image"].get("fallback_detail") is None
