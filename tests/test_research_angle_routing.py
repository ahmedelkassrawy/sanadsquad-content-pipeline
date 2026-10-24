"""Configured marketing angles must select a service line before research."""

from pathlib import Path
import shutil

import pytest

from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline_end_to_end import OfflineModels, search_factory


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_marketing_angle_without_a_service_line_fails_before_research(tmp_path):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    (root / "post_types/marketing-post/angles.yaml").write_text(
        "angles:\n- {id: ANGLE-unknown, name: custom-service-angle}\n", encoding="utf-8"
    )
    models = OfflineModels()
    summary = await run_pipeline(
        root,
        [ResolvedSlot(slot_id="a", type="marketing-post")],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )

    assert summary.slots["a"].outcome.value == "failed_research"
    assert "marketing_service_line_missing" in summary.slots["a"].error
    assert not any(call[2] == "ResearchAction" for call in models.calls)


@pytest.mark.asyncio
async def test_tech_threads_keep_their_configured_kb_routing(tmp_path):
    models = OfflineModels()
    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id="a", type="tech-thread")],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )

    assert summary.shipped == 1
    assert not any(call[2] == "ResearchAction" for call in models.calls)
