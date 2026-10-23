"""Marketing angles merge permitted KB evidence only after allowlisted web research."""

import json
from pathlib import Path
import shutil

import pytest
import yaml

from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from content_pipeline.post_types import PostTypes
from skills.web_search import WebSearch
from test_pipeline_end_to_end import OfflineModels, OfflineSearch, search_factory


ROOT = Path(__file__).resolve().parents[1]


def configured_root(tmp_path, source_ids):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    angles_path = root / "post_types/marketing-post/angles.yaml"
    angles_path.write_text(
        yaml.safe_dump(
            {
                "angles": [
                    {
                        "id": "ANGLE-education",
                        "name": "vulnerability-class",
                        "service_line": "agentic-security",
                        "subject": "vulnerability-class",
                        "kb_allowed": True,
                        "source_ids": source_ids,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_configured_vulnerability_angle_merges_web_and_kb_evidence_with_writer_and_reviewer(
    tmp_path,
):
    root = configured_root(tmp_path, ["SS-03699"])
    models = OfflineModels(
        {
            "education": {
                "writer_text": "Agent tools need restrictions before they can spend.",
                "writer_source_ids": ["SS-03699", "WEB-8693fe686cbfdb80"],
            }
        }
    )

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
    web_sources = [source for source in writer["sources"] if source["kind"] == "web"]
    assert web_sources and all(
        source["url"].startswith("https://github.com/") for source in web_sources
    )
    technical_sources = [source for source in writer["sources"] if source["kind"] == "kb"]
    assert [source["source_id"] for source in technical_sources] == ["SS-03699"]
    evidence = technical_sources[0]
    assert evidence["kind"] == "kb"
    assert "malicious instructions" in evidence["evidence_text"].lower()
    assert "proper restrictions" in evidence["evidence_text"].lower()


@pytest.mark.asyncio
async def test_configured_angle_with_unknown_kb_evidence_is_rejected_before_run(tmp_path):
    root = configured_root(tmp_path, ["SS-99999"])
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


@pytest.mark.asyncio
async def test_open_marketing_angle_uses_its_allowlisted_web_source_before_stats(tmp_path):
    models = OfflineModels()
    searches = []

    def factory(root):
        search = WebSearch(tavily=OfflineSearch(), check_url=lambda url: url, fetch=lambda url: "")
        searches.append(search)
        return search

    output = tmp_path / "run"
    await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id="web3", type="marketing-post", topic_id="ANGLE-web3-security")],
        output,
        model_factory=models.factory,
        search_factory=factory,
    )

    records = json.loads((output / "research_log.json").read_text(encoding="utf-8"))
    pack = next(record["pack"] for record in records if record["pack"])
    assert {source["kind"] for source in pack["sources"]} == {"web", "stat"}
    assert searches[0].service_line == "web3-security"
    assert "github.com" in searches[0].allowed_domains


@pytest.mark.asyncio
async def test_focused_marketing_slot_resolves_service_line_or_fails_before_search(tmp_path):
    models = OfflineModels()
    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id="focused", type="marketing-post", focus="API access controls")],
        tmp_path / "focused",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.slots["focused"].outcome.value != "failed_research"
    assert any(call[2] == "ResearchAction" for call in models.calls)

    failed = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id="unknown", type="marketing-post", focus="unclassified garden topic")],
        tmp_path / "unknown",
        model_factory=OfflineModels().factory,
        search_factory=search_factory,
    )
    assert failed.slots["unknown"].outcome.value == "failed_research"
    assert "marketing_service_line_missing" in failed.slots["unknown"].error


@pytest.mark.asyncio
async def test_marketing_rules_are_in_the_prompt_and_snapshot(tmp_path):
    models = OfflineModels()
    output = tmp_path / "run"
    await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id="web3", type="marketing-post", topic_id="ANGLE-web3-security")],
        output,
        model_factory=models.factory,
        search_factory=search_factory,
    )

    assert (output / "snapshot/inputs/marketing-rules.md").read_text(encoding="utf-8") == (
        ROOT / "inputs/marketing-rules.md"
    ).read_text(encoding="utf-8")
    assert "# Marketing Guide" in PostTypes(ROOT).prompt("marketing_writer", "marketing-post")
