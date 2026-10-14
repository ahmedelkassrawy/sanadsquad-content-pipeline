from pathlib import Path
import shutil

import pytest
import yaml

from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline import OfflineModels, search_factory


ROOT = Path(__file__).resolve().parents[1]


def configured_root(tmp_path, source_ids):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    angles_path = root / "post_types/marketing-post/angles.yaml"
    angles_path.write_text(yaml.safe_dump({"angles": [{"id": "ANGLE-education", "name": "education-to-cta", "source_ids": source_ids}]}), encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_configured_education_angle_shares_kb_mechanism_and_mitigation_with_writer_and_reviewer(tmp_path):
    root = configured_root(tmp_path, ["KB-01"])
    models = OfflineModels()

    summary = await run_pipeline(
        root,
        [ResolvedSlot(slot_id="education", type="marketing-post", topic_id="ANGLE-education")],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )

    assert summary.shipped == 1
    writer = next(call[3]["research"] for call in models.calls if call[2] == "Draft")
    reviewer = next(call[3]["research"] for call in models.calls if call[2] == "ReviewAssessment")
    assert writer == reviewer
    assert writer["topic_id"] == "ANGLE-education"
    technical_sources = [source for source in writer["sources"] if source["kind"] == "kb"]
    assert [source["source_id"] for source in technical_sources] == ["KB-01"]
    evidence = technical_sources[0]
    assert evidence["kind"] == "kb"
    assert "reenter" in evidence["evidence_text"].lower()
    assert "checks-effects-interactions" in evidence["evidence_text"].lower()


@pytest.mark.asyncio
async def test_configured_angle_with_unknown_kb_evidence_is_rejected_before_run(tmp_path):
    root = configured_root(tmp_path, ["KB-999"])
    models = OfflineModels()

    with pytest.raises(ValueError, match="unknown knowledge-base source"):
        await run_pipeline(
            root,
            [ResolvedSlot(slot_id="education", type="marketing-post", topic_id="ANGLE-education")],
            tmp_path / "run",
            model_factory=models.factory,
            search_factory=search_factory,
        )
    assert not (tmp_path / "run").exists()
    assert models.calls == []


@pytest.mark.asyncio
async def test_configured_angle_rejects_empty_evidence_list(tmp_path):
    root = configured_root(tmp_path, [])
    models = OfflineModels()

    with pytest.raises(ValueError, match="configured angle evidence needs unique source ids"):
        # Startup rejects malformed evidence configuration before creating the run.
        await run_pipeline(
            root,
            [ResolvedSlot(slot_id="education", type="marketing-post", topic_id="ANGLE-education")],
            tmp_path / "run",
            model_factory=models.factory,
            search_factory=search_factory,
        )
    assert not (tmp_path / "run").exists()
    assert models.calls == []
