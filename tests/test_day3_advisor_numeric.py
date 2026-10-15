import json
from pathlib import Path
import shutil
import pytest
from content_pipeline.pipeline import run_pipeline
from test_day3_delivery import DeliveryModels, one_slot
from test_pipeline import search_factory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "mode",
    [
        "borrowed-number",
        "changed-claim",
        "changed-numeric-wording",
        "partial-numeric-line",
        "safe-prose",
        "unchanged-numeric",
    ],
)
async def test_advisor_numeric_edits_fail_closed_without_altering_shipped_content(tmp_path, mode):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    writer = root / "post_types/tech-thread/writer.md"
    numeric_line = "Keep the 12 fictional audits explicitly labelled fictional."
    writer.write_text(writer.read_text() + numeric_line + "\n")
    originals = {
        path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*.md")
    }
    models = DeliveryModels()

    def factory(name, slot_id, context):
        client = models.factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            result = await original(messages, schema, max_tokens)
            if schema.__name__ == "AdvisorOutput":
                request = json.loads(
                    next(
                        message["content"]
                        for message in reversed(messages)
                        if message["content"].startswith('{"slot_id"')
                    )
                )
                path = (
                    "inputs/brand-voice.md"
                    if mode in {"borrowed-number", "safe-prose"}
                    else "post_types/tech-thread/writer.md"
                )
                file = request["instructions"][path]
                before = (
                    "Use a calm, precise engineering voice."
                    if path.startswith("inputs/")
                    else numeric_line
                )
                if mode == "partial-numeric-line":
                    before = "fictional audits"
                after = {
                    "borrowed-number": "Sentinel Grove prevents 12 percent of all smart contract hacks.",
                    "changed-claim": "Sentinel Grove prevents 12 percent of all smart contract hacks.",
                    "changed-numeric-wording": "Keep the 12 real audits explicitly labelled verified.",
                    "partial-numeric-line": "percent of hacks prevented",
                    "safe-prose": "Use a calm, precise engineering voice and explain the evidence clearly.",
                    "unchanged-numeric": numeric_line,
                }[mode]
                result["parsed"]["edits"] = [
                    {
                        "path": path,
                        "source_sha256": file["sha256"],
                        "before": before,
                        "after": after,
                    }
                ]
            return result

        client.transport = transport
        return client

    output = tmp_path / "run"
    summary = await run_pipeline(
        root, one_slot(), output, model_factory=factory, search_factory=search_factory
    )
    assert (
        summary.shipped == 1 and summary.confirmed_tokens == 250 and summary.uncertain_tokens == 0
    )
    assert originals == {
        path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*.md")
    }
    assert json.loads((output / "posts.json").read_text())[0]["tweets"] == [
        f"{i}/5 State before calls." for i in range(1, 6)
    ]
    record = json.loads((output / "advisor.json").read_text())[0]
    patch = (output / "advice.patch").read_text()
    if mode in {"safe-prose", "unchanged-numeric"}:
        assert summary.advisor == record["status"] == "completed"
        if mode == "safe-prose":
            assert (
                "+Use a calm, precise engineering voice and explain the evidence clearly." in patch
            )
        else:
            assert patch == ""
    else:
        assert summary.advisor == record["status"] == "failed"
        assert record["output"] is None and patch == ""
        assert "prevents 12 percent" not in (output / "advice.md").read_text()
