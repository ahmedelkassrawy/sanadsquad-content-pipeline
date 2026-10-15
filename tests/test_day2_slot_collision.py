import json
from pathlib import Path
import pytest
from PIL import Image
from content_pipeline.cli import main
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]


async def test_case_colliding_slots_python_refused_before_initialization(tmp_path):
    initialized = []
    models = OfflineModels()

    def factory(name, slot_id, context):
        initialized.append((name, slot_id))
        return models.factory(name, slot_id, context)

    def search(root):
        initialized.append("search")
        return search_factory(root)

    error = None
    try:
        await run_pipeline(
            ROOT,
            [ResolvedSlot(slot_id=id, type="tech-thread") for id in ["A", "a"]],
            tmp_path / "run",
            model_factory=factory,
            search_factory=search,
        )
    except ValueError as caught:
        error = caught
    assert error is not None, "case-colliding slots were accepted"
    assert initialized == []
    assert models.calls == []
    assert not (tmp_path / "run").exists()


def test_case_colliding_slots_cli_plan_refused_before_initialization(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"slots": [{"slot_id": id, "type": "tech-thread"} for id in ["A", "a"]]})
    )
    initialized = []
    models = OfflineModels()

    def factory(name, slot_id, context):
        initialized.append((name, slot_id))
        return models.factory(name, slot_id, context)

    def search(root):
        initialized.append("search")
        return search_factory(root)

    assert (
        main(
            ["--root", str(ROOT), "--plan", str(plan), "--output", str(tmp_path / "run")],
            model_factory=factory,
            search_factory=search,
        )
        == 2
    ), "case-colliding plan was accepted"
    assert initialized == []
    assert models.calls == []
    assert not (tmp_path / "run").exists()


async def test_distinct_mixed_case_slots_keep_independent_image_and_spec_files(tmp_path):
    models = OfflineModels({"A": {"visual_api": True}, "b": {"visual_api": True}})
    output = tmp_path / "run"
    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id=id, type="tech-thread") for id in ["A", "b"]],
        output,
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.shipped == 2
    records = json.loads((output / "visuals.json").read_text())
    paths = [output / record["result"]["path"] for record in records]
    assert records[0]["spec"]["overlay_text"] != records[1]["spec"]["overlay_text"]
    assert not paths[0].samefile(paths[1])
    assert not paths[0].with_suffix(".json").samefile(paths[1].with_suffix(".json"))
    for record, path in zip(records, paths):
        assert json.loads(path.with_suffix(".json").read_text()) == record["spec"]
        with Image.open(path) as image:
            assert image.format == "PNG" and image.size == (1200, 675)
            image.verify()
