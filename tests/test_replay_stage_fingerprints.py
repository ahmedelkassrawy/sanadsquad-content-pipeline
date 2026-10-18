"""Resume reuses a saved stage only when its input fingerprint matches, else refuses."""

import asyncio
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from langgraph.errors import NodeCancelledError
from test_pipeline_resume_and_advisor_delivery import (
    ROOT,
    DeliveryModels,
    one_slot,
    records,
    search_factory,
)

from content_pipeline import cli, pipeline
from content_pipeline.contracts import ReviewRecord
from content_pipeline.durability import RunLock
from content_pipeline.pipeline import Pipeline, run_pipeline
from content_pipeline.schemas import export
from content_pipeline.stage_replay import MISMATCH, SavedStageMismatch, StageReplay
from content_pipeline.trail import Trail

LEGACY_RUN = Path(__file__).parent / "fixtures" / "legacy_run" / "run"
TAMPERED = "0" * 64


def crash_after_record(name):
    """Raise after the first record of one trail is saved, so its node never checkpoints."""
    original = Pipeline.record
    crashed = False

    async def record(pipeline_, trail, entry):
        nonlocal crashed
        await original(pipeline_, trail, entry)

        if trail == name and not crashed:
            crashed = True

            raise asyncio.CancelledError()

    return record


async def crash_after(tmp_path, name, models=None):
    """Run one slot until the first record of a trail is saved, then crash."""
    models = models or DeliveryModels()
    output = tmp_path / "run"

    with (
        patch.object(Pipeline, "record", crash_after_record(name)),
        pytest.raises(NodeCancelledError),
    ):
        await run_pipeline(
            ROOT,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
        )

    return models, output


def tamper(folder, name, change):
    trail = records(folder, name)

    change(trail)

    (folder / (name + ".json")).write_text(json.dumps(trail), encoding="utf-8")

    return trail


def forbidden(*args):
    pytest.fail("a refused or completed resume reached the model transport")


async def resume(output, models, **kwargs):
    return await run_pipeline(
        ROOT, resume=output, model_factory=models.factory, search_factory=search_factory, **kwargs
    )


def schemas(models, start=0):
    return [call[2] for call in models.calls[start:]]


async def test_tampered_draft_fingerprint_is_refused_before_any_call(tmp_path):
    models, output = await crash_after(tmp_path, "drafts")
    saved = tamper(output, "drafts", lambda trail: trail[0].update({"input_sha256": TAMPERED}))
    before = len(models.calls)

    with pytest.raises(ValueError, match=MISMATCH):
        await resume(output, models)

    assert records(output, "drafts") == saved
    assert len(models.calls) == before


async def test_tampered_draft_audit_is_refused_before_any_call(tmp_path):
    models, output = await crash_after(tmp_path, "drafts")
    saved = tamper(output, "drafts", lambda trail: trail[0]["audit"].update({"accepted": False}))
    before = len(models.calls)

    with pytest.raises(ValueError, match=MISMATCH):
        await resume(output, models)

    assert records(output, "drafts") == saved
    assert len(models.calls) == before


async def test_untouched_draft_record_is_reused_and_keeps_its_fingerprint(tmp_path):
    models, output = await crash_after(tmp_path, "drafts")
    saved = records(output, "drafts")
    before = len(models.calls)

    summary = await resume(output, models)

    assert saved[0]["input_sha256"]
    assert summary.shipped == 1
    assert records(output, "drafts") == saved
    assert schemas(models, before) == ["ReviewAssessment", "VisualOutput", "AdvisorOutput"]


async def test_saved_writer_failure_is_reused_and_a_tampered_one_is_refused(tmp_path):
    def models_with_bad_writer():
        models = DeliveryModels()
        models.behavior = {"a": {"writer_mismatch": True}}

        return models

    models, output = await crash_after(tmp_path / "kept", "drafts", models_with_bad_writer())
    saved = records(output, "drafts")
    before = len(models.calls)

    assert [entry["record_id"] for entry in saved] == ["a/writer/1/1"]

    summary = await resume(output, models)

    assert summary.slots["a"].outcome.value == "dropped_quality"
    assert [call[3]["attempt"] for call in models.calls[before:] if call[2] == "Draft"] == [2]

    models, output = await crash_after(tmp_path / "refused", "drafts", models_with_bad_writer())

    tamper(output, "drafts", lambda trail: trail[0].update({"input_sha256": TAMPERED}))

    before = len(models.calls)

    with pytest.raises(ValueError, match=MISMATCH):
        await resume(output, models)

    assert len(models.calls) == before


async def test_tampered_review_fingerprint_ends_the_slot_without_another_call(tmp_path):
    models, output = await crash_after(tmp_path, "review_report")
    saved = tamper(
        output, "review_report", lambda trail: trail[0].update({"input_sha256": TAMPERED})
    )
    before = len(models.calls)

    summary = await resume(output, models)

    assert summary.slots["a"].outcome.value == "failed_api"
    assert summary.shipped == 0
    assert records(output, "review_report") == saved
    assert "ReviewAssessment" not in schemas(models, before)
    assert "VisualOutput" not in schemas(models, before)


async def test_tampered_visual_fingerprint_refuses_the_resume_before_another_call(tmp_path):
    models, output = await crash_after(tmp_path, "visuals")
    saved = tamper(output, "visuals", lambda trail: trail[0].update({"input_sha256": TAMPERED}))
    before = len(models.calls)

    with pytest.raises(ValueError, match=MISMATCH):
        await resume(output, models)

    assert records(output, "visuals") == saved
    assert len(models.calls) == before


async def test_tampered_advisor_fingerprint_fails_the_advisor_and_keeps_the_record(tmp_path):
    models, output = await crash_after(tmp_path, "advisor")
    saved = tamper(output, "advisor", lambda trail: trail[0].update({"input_sha256": TAMPERED}))
    before = len(models.calls)

    summary = await resume(output, models)

    assert summary.shipped == 1
    assert (summary.advisor, summary.advisor_reason) == ("failed", "failed_api")
    assert records(output, "advisor") == saved
    assert len(models.calls) == before


async def test_untouched_visual_and_review_records_are_reused_without_calls(tmp_path):
    models, output = await crash_after(tmp_path, "visuals")
    before = len(models.calls)

    summary = await resume(output, models)

    assert summary.shipped == 1
    assert schemas(models, before) == ["AdvisorOutput"]


async def test_trail_record_with_the_same_id_and_different_content_raises(tmp_path):
    trail = Trail(tmp_path / "drafts.json")

    await trail.append("x/writer/1/1", {"text": "first"})
    await trail.append("x/writer/1/1", {"text": "first"})

    with pytest.raises(ValueError, match="duplicate trail id with different content"):
        await trail.append("x/writer/1/1", {"text": "second"})

    assert json.loads((tmp_path / "drafts.json").read_text()) == [
        {"record_id": "x/writer/1/1", "text": "first"}
    ]


async def test_resume_with_a_changed_saved_final_record_raises(tmp_path):
    models, output = await crash_after(tmp_path, "final")
    saved = tamper(
        output, "final", lambda trail: trail[0]["draft"]["tweets"][0].update({"text": "Rewritten"})
    )

    with pytest.raises(ValueError, match="duplicate trail id with different content"):
        await resume(output, models)

    assert records(output, "final") == saved


async def test_resume_with_an_unchanged_saved_final_record_keeps_it(tmp_path):
    models, output = await crash_after(tmp_path, "final")
    saved = records(output, "final")
    before = len(models.calls)

    summary = await resume(output, models)

    assert summary.shipped == 1
    assert records(output, "final") == saved
    assert schemas(models, before) == ["AdvisorOutput"]


async def test_completed_resume_makes_zero_model_calls(tmp_path):
    models = DeliveryModels()
    output = tmp_path / "run"
    initial = await run_pipeline(
        ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
    )
    calls = len(models.calls)

    resumed = await run_pipeline(
        ROOT, resume=output, model_factory=forbidden, search_factory=forbidden
    )

    assert resumed == initial
    assert len(models.calls) == calls


def test_cli_completed_resume_makes_zero_model_calls(tmp_path, capsys):
    models = DeliveryModels()
    output = tmp_path / "run"

    asyncio.run(
        run_pipeline(
            ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
        )
    )

    status = cli.main(
        ["--root", str(ROOT), "--resume", str(output)],
        model_factory=forbidden,
        search_factory=forbidden,
    )

    assert status == 0
    assert json.loads(capsys.readouterr().out) == json.loads(
        (output / "run_summary.json").read_text()
    )


def count_preflights(monkeypatch):
    seen = []

    def counted(original):
        def read(run_dir, budget_overrides=None):
            seen.append(Path(run_dir))

            return original(run_dir, budget_overrides)

        return read

    monkeypatch.setattr(pipeline, "load_resume_inputs", counted(pipeline.load_resume_inputs))
    monkeypatch.setattr(cli, "resume_inputs", counted(cli.resume_inputs))

    return seen


async def interrupted_run(tmp_path):
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

    return models, output


async def test_public_resume_reads_the_resume_inputs_once(tmp_path, monkeypatch):
    models, output = await interrupted_run(tmp_path)
    seen = count_preflights(monkeypatch)

    assert (await resume(output, models)).shipped == 1
    assert seen == [output.resolve()]


async def test_resume_inputs_are_read_while_the_run_is_owned(tmp_path, monkeypatch):
    models, output = await interrupted_run(tmp_path)
    owned = []
    original = pipeline.load_resume_inputs

    def read_while_owned(run_dir, budget_overrides=None):
        with pytest.raises(ValueError, match="run already owned"), RunLock(run_dir):
            pass

        owned.append(run_dir)

        return original(run_dir, budget_overrides)

    monkeypatch.setattr(pipeline, "load_resume_inputs", read_while_owned)

    received = []

    summary = await resume(output, models, on_resume_inputs=received.append)

    assert summary.shipped == 1
    assert owned == [output.resolve()]
    assert [inputs[3].run_id for inputs in received] == [output.name]


async def test_resume_budget_overrides_are_applied_in_the_one_read(tmp_path, monkeypatch):
    models, output = await interrupted_run(tmp_path)
    seen = count_preflights(monkeypatch)

    await resume(output, models, budget_overrides={"run_token_budget": 450000})

    assert seen == [output.resolve()]
    assert json.loads((output / "runtime-settings.json").read_text())["run_token_budget"] == 450000


async def test_an_intervening_resume_cannot_roll_the_budget_back(tmp_path):
    models, output = await interrupted_run(tmp_path)
    original = pipeline._prepare_run_inputs
    intervened = []

    def intervene(output_dir, budget):
        def run():
            with pytest.raises(RuntimeError, match="interrupted"):
                asyncio.run(
                    run_pipeline(
                        ROOT,
                        resume=output_dir,
                        budget_overrides={"run_token_budget": budget},
                        model_factory=models.factory,
                        search_factory=search_factory,
                        interrupt_after=["topic_selection"],
                    )
                )

        worker = threading.Thread(target=run)

        worker.start()
        worker.join()

    def prepare_then_intervene(*args):
        prepared = original(*args)

        if not intervened:
            intervened.append(True)

            intervene(prepared[0], 450000)

        return prepared

    with patch.object(pipeline, "_prepare_run_inputs", prepare_then_intervene):
        assert (await resume(output, models)).shipped == 1

    assert intervened
    assert json.loads((output / "runtime-settings.json").read_text())["run_token_budget"] == 450000


def test_cli_resume_reads_the_resume_inputs_once_and_applies_the_budget(tmp_path, monkeypatch):
    models, output = asyncio.run(interrupted_run(tmp_path))
    seen = count_preflights(monkeypatch)

    status = cli.main(
        ["--root", str(ROOT), "--resume", str(output), "--token-budget", "450000"],
        model_factory=models.factory,
        search_factory=search_factory,
    )

    assert status == 0
    assert seen == [output.resolve()]
    assert json.loads((output / "runtime-settings.json").read_text())["run_token_budget"] == 450000


def test_cli_resume_estimate_reads_the_resume_inputs_once_without_running(tmp_path, monkeypatch):
    _, output = asyncio.run(interrupted_run(tmp_path))
    seen = count_preflights(monkeypatch)

    status = cli.main(
        ["--root", str(ROOT), "--resume", str(output), "--estimate"],
        model_factory=forbidden,
        search_factory=forbidden,
    )

    assert status == 0
    assert seen == [output]


async def completed_run(tmp_path):
    models = DeliveryModels()
    output = tmp_path / "run"

    await run_pipeline(
        ROOT, one_slot(), output, model_factory=models.factory, search_factory=search_factory
    )

    return models, output


def tamper_research_receipt(output):
    path = output / "research-replay.json"
    receipts = json.loads(path.read_text(encoding="utf-8"))

    next(iter(receipts.values())).update({"pack_sha256": TAMPERED})

    path.write_text(json.dumps(receipts), encoding="utf-8")


def tamper_visual_sidecar(output):
    path = next((output / "images").glob("*.json"))
    spec = json.loads(path.read_text(encoding="utf-8"))

    spec["concept"] = "Something else"

    path.write_text(json.dumps(spec), encoding="utf-8")


TAMPERS = {
    "research receipt": (tamper_research_receipt, MISMATCH),
    "draft fingerprint": (
        lambda output: tamper(
            output, "drafts", lambda trail: trail[-1].update({"input_sha256": TAMPERED})
        ),
        MISMATCH,
    ),
    "review fingerprint": (
        lambda output: tamper(
            output, "review_report", lambda trail: trail[-1].update({"input_sha256": TAMPERED})
        ),
        MISMATCH,
    ),
    "visual fingerprint": (
        lambda output: tamper(
            output, "visuals", lambda trail: trail[-1].update({"input_sha256": TAMPERED})
        ),
        MISMATCH,
    ),
    "visual sidecar": (tamper_visual_sidecar, "saved visual artifact mismatch"),
    "advisor fingerprint": (
        lambda output: tamper(
            output, "advisor", lambda trail: trail[-1].update({"input_sha256": TAMPERED})
        ),
        MISMATCH,
    ),
    "final record": (
        lambda output: tamper(
            output, "final", lambda trail: trail[-1]["draft"]["tweets"][0].update({"text": "Hi"})
        ),
        MISMATCH,
    ),
}


@pytest.mark.parametrize("name", list(TAMPERS))
async def test_tampered_completed_run_is_refused_before_any_model_call(tmp_path, name):
    models, output = await completed_run(tmp_path)
    change, message = TAMPERS[name]

    change(output)

    calls = len(models.calls)

    with pytest.raises(ValueError, match=message):
        await run_pipeline(ROOT, resume=output, model_factory=forbidden, search_factory=forbidden)

    assert len(models.calls) == calls


@pytest.mark.parametrize("field", ["drafts", "review_report"])
async def test_tampered_checkpointed_stage_is_refused_before_the_next_call(tmp_path, field):
    models = DeliveryModels()
    output = tmp_path / "run"

    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            ROOT,
            one_slot(),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            interrupt_after=["lane:" + ("validate" if field == "drafts" else "review")],
        )

    saved = tamper(output, field, lambda trail: trail[-1].update({"input_sha256": TAMPERED}))
    calls = len(models.calls)

    with pytest.raises(ValueError, match=MISMATCH):
        await resume(output, models)

    assert records(output, field) == saved
    assert len(models.calls) == calls


async def test_run_folder_written_before_draft_fingerprints_still_resumes(tmp_path):
    output = tmp_path / "run"

    shutil.copytree(LEGACY_RUN, output)

    legacy = records(output, "drafts")
    models = DeliveryModels()

    assert [entry["record_id"] for entry in legacy] == ["a/validator/1/1"]
    assert "input_sha256" not in legacy[0]

    summary = await resume(output, models)

    assert summary.shipped == 1
    assert schemas(models) == ["ReviewAssessment", "VisualOutput", "AdvisorOutput"]
    assert records(output, "drafts") == legacy


def test_committed_schemas_describe_the_saved_formats(tmp_path):
    export(tmp_path)

    committed = Path(__file__).parents[1] / "schemas"

    assert {path.name for path in tmp_path.glob("*.json")} == {
        path.name for path in committed.glob("*.json")
    }

    for path in tmp_path.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads(
            (committed / path.name).read_text(encoding="utf-8")
        ), path.name


def test_replay_interface_names_the_refusal_and_allows_legacy_only_when_asked(tmp_path):
    run_dir = tmp_path / "run"
    trails = {"review_report": Trail(run_dir / "review_report.json")}

    run_dir.mkdir()
    trails["review_report"].path.write_text(
        json.dumps([{"record_id": "a/reviewer/1/1", "slot_id": "a", "attempt": 1}]),
        encoding="utf-8",
    )

    replay = StageReplay(None, trails, [])
    request = type("Request", (), {"model_dump_json": lambda self: "{}"})()

    assert replay.reuse("review_report", "a/reviewer/9/1", ReviewRecord, request) is None
    assert replay.reuse(
        "review_report", "a/reviewer/1/1", ReviewRecord, request, legacy_ok=True
    ).legacy

    with pytest.raises(SavedStageMismatch, match=MISMATCH):
        replay.reuse("review_report", "a/reviewer/1/1", ReviewRecord, request)
