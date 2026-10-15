import json
from pathlib import Path
import shutil
import pytest
from content_pipeline.config import load_brand
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "type_name,topic_id,topic_name,source_kind",
    [
        ("marketing-post", "KB-01", "custom-service-angle", "stat"),
        ("marketing-post", "KB-custom", "custom-service-angle", "stat"),
        ("marketing-post", "ANGLE-service", "custom-service-angle", "stat"),
        ("tech-thread", None, None, "kb"),
    ],
)
async def test_public_runner_routes_assigned_topics_by_configured_source(
    tmp_path, type_name, topic_id, topic_name, source_kind
):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    if type_name == "marketing-post":
        (root / "post_types/marketing-post/angles.yaml").write_text(
            f"angles:\n- {{id: {topic_id}, name: {topic_name}}}\n"
        )
    models = OfflineModels()
    output = tmp_path / "run"
    summary = await run_pipeline(
        root,
        [ResolvedSlot(slot_id="a", type=type_name)],
        output,
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.shipped == 1, "valid angle ID was routed outside its configured pool"
    post = json.loads((output / "posts.json").read_text())[0]
    writer = next(call[3]["research"] for call in models.calls if call[2] == "Draft")
    reviewer = next(call[3]["research"] for call in models.calls if call[2] == "ReviewAssessment")
    assert reviewer == writer
    assert {source["kind"] for source in writer["sources"]} == {source_kind}
    assert post["topic_name"] == writer["topic_name"]
    assert post["topic_id"] == writer["topic_id"] == summary.slots["a"].topic_id
    assert not any(call[2] == "ResearchAction" for call in models.calls)
    if source_kind == "stat":
        assert post["topic_id"] == topic_id
        assert post["topic_name"] == topic_name
        rules, _ = load_brand(root / "inputs/brand-voice.md")
        assert {
            source["source_id"]: source["evidence_text"] for source in writer["sources"]
        } == rules.approved_stats
        assert post["tweets"] == ["Sentinel Grove completed 12 fictional audits."]
    else:
        entries = json.loads((root / "inputs/kb.json").read_text())["entries"]
        entry = next(entry for entry in entries if entry["id"] == post["topic_id"])
        assert post["topic_name"] == entry["name"]
        assert writer["sources"][0]["source_id"] == entry["id"]
