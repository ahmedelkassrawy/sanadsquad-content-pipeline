"""Marketing research must use only its offered web tool and data-loaded seeds."""

import json
from pathlib import Path

import pytest

from content_pipeline.config import load_sources
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from content_pipeline.post_types import PostTypes
from skills.web_search import FixtureSearch, WebSearch
from test_pipeline_end_to_end import OfflineModels


ROOT = Path(__file__).resolve().parents[1]


def fixture_search(root):
    search = WebSearch(
        fixture=FixtureSearch(root / "inputs/search-fixtures"), check_url=lambda url: url
    )
    search.registry = load_sources(root / "inputs/sources.json")
    return search


@pytest.mark.asyncio
async def test_marketing_refuses_unavailable_kb_tool_then_ships_web_pack(tmp_path):
    types = PostTypes(ROOT)
    angle = next(angle for angle in types.angles("marketing-post") if not angle.kb_allowed)
    models = OfflineModels(
        {
            "marketing": {
                "research_actions": ["search_kb", "web_search", "finish"],
                "research_query": angle.seed_query,
            }
        }
    )
    output = tmp_path / "run"

    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id="marketing", type="marketing-post", topic_id=angle.id)],
        output,
        model_factory=models.factory,
        search_factory=fixture_search,
    )

    assert summary.slots["marketing"].outcome.value == "shipped"
    records = json.loads((output / "research_log.json").read_text(encoding="utf-8"))
    refused = next(record for record in records if record["action"] == "search_kb")
    assert refused["failure"] == "tool_unavailable:search_kb"
    pack = next(record["pack"] for record in records if record["pack"])
    assert any(source["kind"] == "web" for source in pack["sources"])
    turns = [call[3] for call in models.calls if call[2] == "ResearchAction"]
    assert all(turn["available_actions"] == ["web_search", "finish"] for turn in turns)


def test_marketing_seed_queries_use_configured_short_query_and_fixture_results():
    types = PostTypes(ROOT)
    hints = (ROOT / "post_types/marketing-post/research.md").read_text(encoding="utf-8")

    for angle in types.angles("marketing-post"):
        assert angle.seed_query
        assert angle.seed_query != hints
        search = fixture_search(ROOT)
        lines = [angle.service_line] if angle.service_line else angle.service_lines
        registry = load_sources(ROOT / "inputs/sources.json")
        search.allowed_domains = set().union(*(registry.domains_for(line) for line in lines))
        search.service_line = lines[0]
        results = search.search(angle.seed_query)
        assert results
        assert all(result.status == "ok" and result.url for result in results)
