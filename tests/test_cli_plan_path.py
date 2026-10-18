"""The brief's exact command, `--plan plan.json`, finds the plan in inputs/."""

import json
from pathlib import Path

from content_pipeline.cli import main
from test_pipeline_end_to_end import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]


def test_bare_plan_name_falls_back_to_the_inputs_folder(tmp_path):
    output = tmp_path / "week-42"

    status = main(
        ["--root", str(ROOT), "--plan", "plan.json", "--output", str(output)],
        model_factory=OfflineModels().factory,
        search_factory=search_factory,
    )

    planned = json.loads((ROOT / "inputs/plan.json").read_text())["slots"]
    resolved = json.loads((output / "plan.resolved.json").read_text())

    assert status == 0
    assert (output / "posts.csv").exists()
    assert len(resolved["slots"]) == len(planned)
