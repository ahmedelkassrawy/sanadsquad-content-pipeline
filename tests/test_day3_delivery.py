import asyncio
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import pytest
from langgraph.errors import NodeCancelledError
from content_pipeline.cli import main
from content_pipeline.config import Settings
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]


class DeliveryModels(OfflineModels):
    def __init__(self, advisor="valid", cancel_writer=False):
        super().__init__()
        self.advisor = advisor
        self.cancel_writer = cancel_writer

    def factory(self, name, slot_id, context):
        client = super().factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            if schema.__name__ == "Draft" and self.cancel_writer:
                self.cancel_writer = False
                raise asyncio.CancelledError()
            if schema.__name__ != "AdvisorOutput":
                return await original(messages, schema, max_tokens)
            request = json.loads(
                next(
                    message["content"]
                    for message in reversed(messages)
                    if message["content"].startswith('{"slot_id"')
                )
            )
            self.calls.append((name, slot_id, schema.__name__, request, max_tokens))
            if self.advisor == "api":
                raise RuntimeError("synthetic-advisor-secret")
            path = next(path for path in request["instructions"] if path.endswith("writer.md"))
            file = request["instructions"][path]
            before = file["text"].splitlines(keepends=True)[0]
            edits = [
                {
                    "path": path,
                    "source_sha256": file["sha256"],
                    "before": before,
                    "after": before + "Prefer a clear opening grounded in the supplied sources.\n",
                }
            ]
            if self.advisor == "escape":
                edits[0]["path"] = "../../.env"
            if self.advisor == "stale":
                edits[0]["source_sha256"] = "0" * 64
            if self.advisor == "stat":
                edits[0]["after"] = "Our firm prevents 97.3 percent of hacks.\n"
            if self.advisor == "frontmatter":
                file = request["instructions"]["inputs/brand-voice.md"]
                edits = [
                    {
                        "path": "inputs/brand-voice.md",
                        "source_sha256": file["sha256"],
                        "before": file["text"],
                        "after": "Simplified brand voice.\n",
                    }
                ]
            if self.advisor == "malformed":
                edits = [{"path": path}]
            parsed = {
                "slot_id": "_run",
                "attempt": 1,
                "recommendations": ["Keep openings grounded in supplied evidence."],
                "edits": edits,
            }
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


def one_slot(focus="reentrancy"):
    return [ResolvedSlot(slot_id="a", type="tech-thread", focus=focus)]


def records(folder, name):
    return json.loads((folder / (name + ".json")).read_text())


@pytest.mark.parametrize(
    "boundary",
    [
        "focused_research",
        "topic_selection",
        "lane:write",
        "lane:review",
        "lane:visual",
        "export",
        "advisor",
    ],
)
async def test_resume_actual_graph_boundaries_skip_completed_operations(tmp_path, boundary):
    models = DeliveryModels()
    output = tmp_path / "run"
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            ROOT,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            interrupt_after=[boundary],
        )
    existing_usage = (output / "usage.jsonl").read_bytes()
    summary = await run_pipeline(
        ROOT, resume=output, model_factory=models.factory, search_factory=search_factory
    )
    assert summary.shipped == 1 and summary.advisor == "completed"
    assert [call[2] for call in models.calls] == [
        "ResearchAction",
        "Draft",
        "ReviewAssessment",
        "VisualOutput",
        "AdvisorOutput",
    ]
    assert summary.confirmed_tokens == 250 and summary.uncertain_tokens == 0
    assert (output / "usage.jsonl").read_bytes().startswith(existing_usage)
    assert (summary.external_usage.image_calls, summary.external_usage.pillow_cards) == (1, 1)
    assert records(output, "posts")[0]["tweets"] == [
        f"{i}/5 State before calls." for i in range(1, 6)
    ]
    for name in ["research_log", "drafts", "review_report", "visuals", "final", "advisor"]:
        trail = records(output, name)
        assert len({entry["record_id"] for entry in trail}) == len(trail)
    assert len(records(output, "advisor")) == 1


async def test_completed_resume_is_zero_call_and_preserves_bytes(tmp_path):
    models = DeliveryModels()
    output = tmp_path / "run"
    initial = await run_pipeline(
        ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
    )
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file() and not path.name.startswith("checkpoint.sqlite")
    }

    def forbidden(*args):
        pytest.fail("completed resume initialized transport")

    resumed = await run_pipeline(
        ROOT, resume=output, model_factory=forbidden, search_factory=forbidden
    )
    assert resumed == initial
    assert before == {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file() and not path.name.startswith("checkpoint.sqlite")
    }


async def test_resume_uses_snapshot_preserves_increased_budget_and_renamed_identity(tmp_path):
    root = tmp_path / "root"
    for name in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / name, root / name)
    models = DeliveryModels()
    output = tmp_path / "original"
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            root,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            interrupt_after=["lane:write"],
        )
    (root / "inputs/brand-voice.md").write_text("invalid live brand policy")
    (root / "post_types/tech-thread/writer.md").write_text("synthetic-new-policy")
    renamed = tmp_path / "renamed"
    output.rename(renamed)
    summary = await run_pipeline(
        root,
        resume=renamed,
        budget_overrides={"run_token_budget": 300000},
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.run_id == "original" and summary.shipped == 1
    assert json.loads((renamed / "runtime-settings.json").read_text())["run_token_budget"] == 300000
    assert (
        "synthetic-new-policy"
        not in (renamed / "snapshot/post_types/tech-thread/writer.md").read_text()
    )
    assert (
        await run_pipeline(
            root, resume=renamed, model_factory=models.factory, search_factory=search_factory
        )
    ) == summary


async def test_resume_reconciles_crash_charge_and_advances_call_ids(tmp_path):
    output = tmp_path / "run"
    models = DeliveryModels(cancel_writer=True)
    with pytest.raises(NodeCancelledError):
        await run_pipeline(
            ROOT,
            one_slot("unknown"),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
        )
    ledger = records(output, "ledger")
    writer = next(key for key in ledger if "/writer/" in key)
    ledger["unused/writer/1/1"] = {"tokens": 123, "cost_usd": 0.01, "status": "reserved"}
    ledger["lost/writer/1/1"] = {"tokens": 321, "cost_usd": 0.02, "status": "dispatched"}
    (output / "ledger.json").write_text(json.dumps(ledger))
    old_usage = (output / "usage.jsonl").read_bytes()
    assert records(output, "external-usage")["search_calls"] == 1
    summary = await run_pipeline(
        ROOT, resume=output, model_factory=models.factory, search_factory=search_factory
    )
    ledger = records(output, "ledger")
    assert ledger[writer]["status"] == "uncertain"
    assert ledger[writer.rsplit("/", 1)[0] + "/2"]["status"] == "confirmed"
    assert ledger["unused/writer/1/1"]["status"] == "released"
    assert ledger["lost/writer/1/1"]["status"] == "uncertain"
    assert summary.uncertain_tokens == ledger[writer]["tokens"] + 321
    assert summary.external_usage.search_calls == 1 and summary.external_usage.pillow_cards == 1
    assert (output / "usage.jsonl").read_bytes().startswith(old_usage)
    usage = [json.loads(line) for line in (output / "usage.jsonl").read_text().splitlines()]
    assert len({entry["call_id"] for entry in usage}) == len(usage)


async def test_unfinished_research_gather_can_repeat_saved_sibling_call(tmp_path):
    from content_pipeline.pipeline import Pipeline

    models = DeliveryModels()
    saved_sibling = asyncio.Event()
    cancelled = False
    original_record = Pipeline.record

    async def record(pipeline, name, entry):
        await original_record(pipeline, name, entry)
        if name == "research_log" and entry.slot_id == "a" and entry.pack is not None:
            saved_sibling.set()

    original_factory = models.factory

    def factory(name, slot_id, context):
        client = original_factory(name, slot_id, context)
        transport = client.transport

        async def interrupted(messages, schema, max_tokens):
            nonlocal cancelled
            if slot_id == "b" and schema.__name__ == "ResearchAction" and not cancelled:
                await saved_sibling.wait()
                cancelled = True
                raise asyncio.CancelledError()
            return await transport(messages, schema, max_tokens)

        client.transport = interrupted
        return client

    from unittest.mock import patch

    output = tmp_path / "run"
    slots = [ResolvedSlot(slot_id=id, type="tech-thread", focus="reentrancy") for id in ["a", "b"]]
    with patch.object(Pipeline, "record", record):
        with pytest.raises(NodeCancelledError):
            await run_pipeline(
                ROOT, slots, output, model_factory=factory, search_factory=search_factory
            )
    assert any(
        entry["pack"] is not None
        for entry in records(output, "research_log")
        if entry["slot_id"] == "a"
    )
    summary = await run_pipeline(
        ROOT, resume=output, model_factory=factory, search_factory=search_factory
    )
    assert summary.shipped == 2 and summary.uncertain_tokens > 0
    usage = [json.loads(line) for line in (output / "usage.jsonl").read_text().splitlines()]
    assert [
        (entry["call_id"], entry["status"])
        for entry in usage
        if entry["call_id"].startswith("a/research_step_1/")
    ] == [("a/research_step_1/1/1", "confirmed")]
    trail = records(output, "research_log")
    assert len({entry["record_id"] for entry in trail}) == len(trail)


@pytest.mark.parametrize("bad", ["snapshot", "checkpoint", "budget", "settings"])
async def test_invalid_resume_refuses_without_calls_or_writes(tmp_path, bad):
    models = DeliveryModels()
    output = tmp_path / "run"
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            ROOT,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            interrupt_after=["focused_research"],
        )
    if bad == "snapshot":
        (output / "snapshot/inputs/kb.json").write_text("{}")
    if bad == "checkpoint":
        (output / "checkpoint.sqlite").unlink()
    before = {
        path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()
    }

    def forbidden(*args):
        pytest.fail("invalid resume initialized transport")

    kwargs = (
        {"budget_overrides": {"run_token_budget": 1}}
        if bad == "budget"
        else {"settings": Settings()}
        if bad == "settings"
        else {}
    )
    with pytest.raises(ValueError):
        await run_pipeline(
            ROOT, resume=output, model_factory=forbidden, search_factory=forbidden, **kwargs
        )
    assert before == {
        path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()
    }


async def test_same_run_ownership_refuses_before_ledger_recovery(tmp_path):
    from content_pipeline.durability import RunLock

    models = DeliveryModels()
    output = tmp_path / "run"
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            ROOT,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            interrupt_after=["focused_research"],
        )
    before = (output / "ledger.json").read_bytes()
    with RunLock(output):
        with pytest.raises(ValueError, match="owned"):
            await run_pipeline(
                ROOT, resume=output, model_factory=models.factory, search_factory=search_factory
            )
    assert (output / "ledger.json").read_bytes() == before
    assert (
        await run_pipeline(
            ROOT, resume=output, model_factory=models.factory, search_factory=search_factory
        )
    ).shipped == 1


async def test_concurrent_public_resume_is_rejected_while_owner_dispatches(tmp_path):
    models = DeliveryModels()
    output = tmp_path / "run"
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            ROOT,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            interrupt_after=["focused_research"],
        )
    entered = asyncio.Event()
    release = asyncio.Event()

    def factory(name, slot_id, context):
        client = models.factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            if schema.__name__ == "Draft":
                entered.set()
                await release.wait()
            return await original(messages, schema, max_tokens)

        client.transport = transport
        return client

    owner = asyncio.create_task(
        run_pipeline(ROOT, resume=output, model_factory=factory, search_factory=search_factory)
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        before = (output / "ledger.json").read_bytes()

        def forbidden(*args):
            pytest.fail("contending resume initialized provider")

        with pytest.raises(ValueError, match="owned"):
            await run_pipeline(
                ROOT, resume=output, model_factory=forbidden, search_factory=forbidden
            )
        assert (output / "ledger.json").read_bytes() == before
    finally:
        release.set()
        summary = await owner
    assert summary.shipped == 1 and summary.uncertain_tokens == 0


@pytest.mark.parametrize(
    "stage,schema",
    [
        ("review_report", "ReviewAssessment"),
        ("visuals", "VisualOutput"),
        ("advisor", "AdvisorOutput"),
    ],
)
async def test_resume_after_saved_stage_trail_keeps_result_and_artifacts_consistent(
    tmp_path, stage, schema
):
    from content_pipeline.pipeline import Pipeline
    from unittest.mock import patch

    models = DeliveryModels()
    output = tmp_path / "run"
    original = Pipeline.record
    interrupted = False

    async def record(pipeline, name, entry):
        nonlocal interrupted
        await original(pipeline, name, entry)
        if name == stage and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()

    with patch.object(Pipeline, "record", record):
        with pytest.raises(NodeCancelledError):
            await run_pipeline(
                ROOT,
                one_slot(),
                output,
                model_factory=models.factory,
                search_factory=search_factory,
            )
    saved = records(output, stage)
    saved_image = (
        {path.name: path.read_bytes() for path in (output / "images").glob("*")}
        if stage == "visuals"
        else {}
    )
    saved_advice = (output / "advice.patch").read_bytes() if stage == "advisor" else None
    summary = await run_pipeline(
        ROOT, resume=output, model_factory=models.factory, search_factory=search_factory
    )
    assert summary.shipped == 1 and summary.advisor == "completed"
    assert records(output, stage) == saved
    assert len([call for call in models.calls if call[2] == schema]) == 1
    if saved_image:
        assert saved_image == {
            path.name: path.read_bytes() for path in (output / "images").glob("*")
        }
    if saved_advice is not None:
        assert (output / "advice.patch").read_bytes() == saved_advice


@pytest.mark.parametrize(
    "mode", ["valid", "api", "escape", "stale", "stat", "malformed", "frontmatter"]
)
async def test_advisor_suggests_only_bounded_patch_and_never_sinks_export(tmp_path, mode):
    models = DeliveryModels(advisor=mode)
    output = tmp_path / "run"
    original = (ROOT / "post_types/tech-thread/writer.md").read_bytes()
    summary = await run_pipeline(
        ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
    )
    assert summary.shipped == 1
    assert summary.advisor == ("completed" if mode == "valid" else "failed")
    assert summary.advisor_reason == {"valid": None, "api": "failed_api"}.get(mode, "failed_output")
    assert (ROOT / "post_types/tech-thread/writer.md").read_bytes() == original
    assert (output / "snapshot/post_types/tech-thread/writer.md").read_bytes() == original
    assert records(output, "posts")[0]["tweets"] == [
        f"{i}/5 State before calls." for i in range(1, 6)
    ]
    patch = (output / "advice.patch").read_text()
    if mode == "valid":
        assert "--- a/post_types/tech-thread/writer.md" in patch
        assert "+Prefer a clear opening grounded in the supplied sources." in patch
        assert summary.confirmed_tokens == 250
        import subprocess

        check_root = tmp_path / "patch-check"
        target = check_root / "post_types/tech-thread/writer.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        result = subprocess.run(
            ["git", "apply", "--check", str(output / "advice.patch")],
            cwd=check_root,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    else:
        assert patch == ""
    if mode == "malformed":
        assert len([call for call in models.calls if call[2] == "AdvisorOutput"]) == 2
    assert "synthetic-advisor-secret" not in (output / "run_summary.json").read_text()
    assert len(records(output, "advisor")) == 1


def test_public_cli_resume_and_estimate_use_saved_policy_without_initialization(tmp_path):
    models = DeliveryModels()
    output = tmp_path / "run"
    initial = asyncio.run(
        run_pipeline(
            ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
        )
    )

    def forbidden(*args):
        pytest.fail("pure CLI initialized provider")

    assert (
        main(
            ["--root", str(ROOT), "--resume", str(output), "--estimate"],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 0
    )
    assert (
        main(
            ["--root", str(ROOT), "--resume", str(output)],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 0
    )
    assert (
        main(
            [
                "--root",
                str(ROOT),
                "--resume",
                str(output),
                "--model-override",
                "openai:gpt-4.1-mini",
            ],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 2
    )
    assert (
        main(
            ["--root", str(ROOT), "--resume", str(output), "--plan", "inputs/plan.json"],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 2
    )
    assert (
        main(
            ["--root", str(ROOT), "--resume", str(tmp_path / "missing")],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 2
    )
    assert initial.shipped == 1


async def test_advisor_disabled_and_zero_export_budget_failure_are_explicit(tmp_path):
    models = DeliveryModels()
    disabled = await run_pipeline(
        ROOT,
        one_slot(),
        tmp_path / "disabled",
        Settings(advisor_enabled=False),
        models.factory,
        search_factory,
    )
    assert disabled.shipped == 1 and disabled.advisor == "not_enabled"
    assert not any(call[2] == "AdvisorOutput" for call in models.calls)
    failed = await run_pipeline(
        ROOT,
        one_slot(),
        tmp_path / "failed",
        Settings(run_token_budget=1),
        models.factory,
        search_factory,
    )
    assert (
        failed.shipped == 0
        and failed.advisor == "failed"
        and failed.advisor_reason == "failed_budget"
    )
    assert (tmp_path / "failed/posts.csv").read_bytes() == b"Post Text,Image\r\n"


async def test_inline_source_ids_metadata_is_rejected_before_review_and_export(tmp_path):
    models = DeliveryModels()

    def factory(name, slot_id, context):
        client = models.factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            result = await original(messages, schema, max_tokens)
            if schema.__name__ == "Draft":
                for tweet in result["parsed"]["tweets"]:
                    tweet["text"] += " source_ids: [KB-01]"
            return result

        client.transport = transport
        return client

    summary = await run_pipeline(
        ROOT, one_slot(), tmp_path / "run", model_factory=factory, search_factory=search_factory
    )
    assert summary.shipped == 0
    assert summary.slots["a"].outcome.value == "dropped_quality"
    assert summary.slots["a"].attempts == 2
    assert not any(call[2] == "ReviewAssessment" for call in models.calls)
    assert (tmp_path / "run/posts.csv").read_bytes() == b"Post Text,Image\r\n"
    reasons = summary.slots["a"].reasons
    assert any("banned_phrase:source_ids" in reason for reason in reasons)
    assert any("leaked_source_id:KB-01" in reason for reason in reasons)
