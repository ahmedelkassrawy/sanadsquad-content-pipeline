"""Cross-service marketing posts may search two service allowlists and record each source's line."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from content_pipeline.config import load_angles, load_sources
from content_pipeline.contracts import Angle, RunIdentity, SlotState
from content_pipeline.models import (
    Draft,
    ResearchPack,
    ResolvedSlot,
    SourceRecord,
    Tweet,
)
from content_pipeline.pipeline import Pipeline, run_pipeline
from content_pipeline.post_types import PostTypes
from skills import brand_rules
from skills.web_search import FixtureSearch, WebSearch
from test_pipeline_end_to_end import OfflineModels


ROOT = Path(__file__).resolve().parents[1]
CROSS = ["web3-security", "agentic-security"]
WEB3_URL = "https://blog.trailofbits.com/agent-wallet-key-custody/"
AGENT_URL = "https://genai.owasp.org/llmrisk/llm01-prompt-injection/"
BOTH_URL = "https://github.com/example/repo"  # github.com is on both allowlists
OUTSIDE_URL = "https://medium.com/some-post"  # on neither allowlist


def cross_angle(**overrides):
    fields = {"id": "ANGLE-x", "name": "x", "service_lines": CROSS}
    fields.update(overrides)
    return Angle(**fields)


def registry():
    return load_sources(ROOT / "inputs/sources.json")


def cross_searcher(hits):
    """A fixture-backed searcher configured the way the research agent configures it."""

    class Fixture:
        def text(self, query):
            return hits

    reg = registry()
    search = WebSearch(fixture=Fixture(), check_url=lambda url: url)
    search.registry = reg
    search.allowed_domains = reg.domains_for(CROSS[0]) | reg.domains_for(CROSS[1])
    search.service_line = CROSS[0]
    search.service_domains = reg.domains_by_service(CROSS)
    return search


def hit(url):
    return {"url": url, "title": url, "raw_content": "Agent wallets need spending limits."}


# 1. Angle contract -------------------------------------------------------------------------


def test_angle_accepts_two_distinct_service_lines_and_keeps_single_service_unchanged():
    angle = cross_angle()
    assert angle.service_lines == CROSS and angle.service_line is None

    single = Angle(id="ANGLE-y", name="y", service_line="pentesting")
    assert single.service_line == "pentesting" and single.service_lines is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"service_line": "pentesting"},
        {"service_lines": ["web3-security"]},
        {"service_lines": ["web3-security", "pentesting", "agentic-security"]},
        {"service_lines": ["web3-security", "web3-security"]},
        {"service_lines": ["web3-security", "not-a-service"]},
    ],
    ids=["both-fields", "one", "three", "duplicate", "unknown"],
)
def test_angle_rejects_invalid_service_line_combinations(overrides):
    assert cross_angle().service_lines == CROSS  # the valid shape exists on this branch
    with pytest.raises(ValidationError):
        cross_angle(**overrides)


def test_angles_file_without_the_new_field_loads_unchanged(tmp_path):
    root = tmp_path / "repo"
    for name in ("agents", "config", "inputs", "post_types", "schemas"):
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, root / name)
    path = root / "post_types/marketing-post/angles.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["angles"] = [a for a in data["angles"] if "service_lines" not in a]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    types = PostTypes(root)
    angles = load_angles(root, types.spec("marketing-post"))

    assert angles and all(a.service_lines is None and a.service_line for a in angles)
    slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id="ANGLE-pentesting")
    assert types.marketing_service_line(slot) == "pentesting"
    assert types.marketing_service_lines(slot) is None


def test_shipped_angle_declares_web3_plus_agentic_security_and_slot_snapshot_stays_loadable():
    types = PostTypes(ROOT)
    angle = next(a for a in types.angles("marketing-post") if a.id == "ANGLE-agent-wallet-security")
    assert angle.service_lines == CROSS and angle.seed_query and angle.focus_terms

    slot = ResolvedSlot(slot_id="m", type="marketing-post", topic_id=angle.id)
    assert types.marketing_service_line(slot) == "web3-security"
    assert types.marketing_service_lines(slot) == CROSS

    # An old snapshot slot has no service_lines key and must load and dump identically.
    legacy = {"slot_id": "m", "type": "marketing-post", "service_line": "pentesting"}
    loaded = ResolvedSlot.model_validate(legacy)
    assert loaded.service_lines is None
    assert "service_lines" not in loaded.model_dump(mode="json")


# 2. Searcher: union allowlist and per-source service recording -----------------------------


def test_search_allows_either_allowlist_and_records_which_line_each_source_came_from():
    search = cross_searcher([hit(WEB3_URL), hit(AGENT_URL), hit(BOTH_URL)])

    records = search.search("agent wallet")
    by_url = {record.url: record for record in records}

    assert set(by_url) == {WEB3_URL, AGENT_URL, BOTH_URL}
    assert by_url[WEB3_URL].service_lines == ["web3-security"]
    assert by_url[AGENT_URL].service_lines == ["agentic-security"]
    # A domain on both lists records both lines (primary first).
    assert by_url[BOTH_URL].service_lines == CROSS

    fetched = {entry["final_url"]: entry for entry in search.url_log if "final_url" in entry}
    assert fetched[WEB3_URL]["service_lines"] == ["web3-security"]
    assert fetched[AGENT_URL]["service_lines"] == ["agentic-security"]
    assert fetched[BOTH_URL]["service_lines"] == CROSS
    assert search.query_log[0]["service_lines"] == CROSS
    assert search.query_log[0]["service_line"] == "web3-security"


def test_fetch_logs_the_service_line_and_rejects_urls_outside_both_lists():
    search = cross_searcher([])

    _, _, status = search.fetch_url(OUTSIDE_URL)
    assert status == "fetch_failed"
    assert search.fetch_log == []  # nothing was fetched
    assert search.url_log[-1]["reason"] == "policy_rejected"
    assert search.url_log[-1]["service_lines"] == []

    search.fetch = lambda url: "agent wallet text"
    search.fetch_url(AGENT_URL)
    assert search.fetch_log == [
        {"url": AGENT_URL, "service_line": "web3-security", "service_lines": ["agentic-security"]}
    ]

    # Hits outside both lists never become sources.
    outside = cross_searcher([hit(OUTSIDE_URL), hit(AGENT_URL)])
    assert [r.url for r in outside.search("q")] == [AGENT_URL]


def test_denied_domain_is_still_rejected_even_when_on_an_allowlist():
    search = cross_searcher([])
    search.denied_domains = {"genai.owasp.org"}

    _, _, status = search.fetch_url(AGENT_URL)

    assert status == "fetch_failed" and search.fetch_log == []


def test_single_service_search_still_uses_only_its_own_allowlist():
    reg = registry()
    search = cross_searcher([hit(WEB3_URL), hit(AGENT_URL)])
    search.allowed_domains = reg.domains_for("web3-security")
    search.service_domains = {}  # single-service posts never set this

    records = search.search("q")

    assert [r.url for r in records] == [WEB3_URL]
    assert records[0].service_lines == []
    assert all("service_lines" not in entry for entry in search.url_log + search.query_log)


# 3. Separation check -----------------------------------------------------------------------


def web_source(source_id, url, lines):
    return SourceRecord(
        source_id=source_id, kind="web", url=url, title="t", evidence_text="e", service_lines=lines
    )


def separation(sources, cited, **kwargs):
    pack = ResearchPack(slot_id="m", topic_id="ANGLE-x", topic_name="x", sources=sources)
    draft = Draft(slot_id="m", tweets=[Tweet(text="Agents need limits.", source_ids=cited)])
    spec = SimpleNamespace(name="marketing-post")
    return brand_rules.check_research_separation(draft, pack, spec, **kwargs)


def test_separation_accepts_a_source_in_either_service_line_of_the_post():
    for lines in (["web3-security"], ["agentic-security"], CROSS):
        sources = [web_source("WEB-aa", WEB3_URL, lines)]
        assert separation(sources, ["WEB-aa"], service_lines=CROSS) == []


def test_separation_rejects_a_web_source_outside_both_service_lines():
    accepted = [web_source("WEB-aa", AGENT_URL, ["agentic-security"])]
    assert separation(accepted, ["WEB-aa"], service_lines=CROSS) == []

    outside = [web_source("WEB-bb", OUTSIDE_URL, ["pentesting"])]
    assert separation(outside, ["WEB-bb"], service_lines=CROSS) == ["marketing_web_source_required"]

    mixed = accepted + outside
    assert separation(mixed, ["WEB-bb"], service_lines=CROSS) == ["marketing_web_citation_required"]


# 4. Weekly mix counting --------------------------------------------------------------------


def states(*topic_ids):
    return [
        SlotState(slot=ResolvedSlot(slot_id=f"m{i}", type="marketing-post", topic_id=topic_id))
        for i, topic_id in enumerate(topic_ids, 1)
    ]


def mix_pipeline():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.types = PostTypes(ROOT)
    pipeline.context = SimpleNamespace(
        identity=RunIdentity(
            thread_id="t", run_id="r", snapshot_files={}, marketing_week="2024-W02"
        )
    )
    return pipeline


def test_cross_service_post_counts_toward_both_lines_in_the_weekly_mix():
    slots = states("ANGLE-agent-wallet-security", "ANGLE-agentic-security", "ANGLE-web3-security")
    mix_pipeline()._assign_marketing_mix(slots)

    cross, agentic, web3 = (s.slot for s in slots)
    assert cross.service_line == "web3-security" and cross.service_lines == CROSS
    # Each single-service slot now repeats a line the cross-service post already used, so it is
    # provisionally shaped as the news-reaction exception rather than dodging the limit.
    assert agentic.format == "news-reaction"
    assert web3.format == "news-reaction"


def test_cross_service_post_cannot_dodge_the_per_service_limit():
    slots = states("ANGLE-agent-wallet-security", "ANGLE-agentic-security", "ANGLE-web3-security")
    pipeline = mix_pipeline()
    pipeline._assign_marketing_mix(slots)
    for state in slots:
        state.pack = ResearchPack(
            slot_id=state.slot.slot_id,
            topic_id=state.slot.topic_id,
            topic_name=state.slot.topic_id,
            sources=[web_source("WEB-aa", WEB3_URL, [])],
        )
        state.pack.sources[0].published_at = None

    pipeline._enforce_marketing_mix(slots, "viable")

    # Without dated news, the second use of either line is dropped for the service repeat.
    dropped = [s for s in slots if s.outcome is not None]
    assert dropped, "a cross-service post plus same-line posts must trip the repeat rule"
    assert any("service_repeat" in reason for s in dropped for reason in s.reasons)


# 5. End to end through the research agent ---------------------------------------------------


@pytest.mark.asyncio
async def test_research_agent_searches_both_allowlists_and_logs_each_source_line(tmp_path):
    angle = next(
        a for a in PostTypes(ROOT).angles("marketing-post") if a.id == "ANGLE-agent-wallet-security"
    )
    models = OfflineModels(
        {
            "marketing": {
                "research_actions": ["web_search", "finish"],
                "research_query": angle.seed_query,
            }
        }
    )
    searchers = []

    def factory(root):
        search = WebSearch(
            fixture=FixtureSearch(root / "inputs/search-fixtures"), check_url=lambda url: url
        )
        search.registry = load_sources(root / "inputs/sources.json")
        searchers.append(search)
        return search

    output = tmp_path / "run"
    await run_pipeline(
        ROOT,
        [
            ResolvedSlot(
                slot_id="marketing",
                type="marketing-post",
                topic_id=angle.id,
                focus=angle.seed_query,
            )
        ],
        output,
        model_factory=models.factory,
        search_factory=factory,
    )

    records = json.loads((output / "research_log.json").read_text(encoding="utf-8"))
    seen = {
        source["url"]: source["service_lines"]
        for record in records
        for source in record["evidence"]
        if source["kind"] == "web"
    }
    # Both allowlists were searched, and each retained source names the line it came from.
    assert seen[WEB3_URL] == ["web3-security"]
    assert seen[AGENT_URL] == ["agentic-security"]
    pack = next(record["pack"] for record in records if record["pack"])
    web = [source for source in pack["sources"] if source["kind"] == "web"]
    assert web and all(source["service_lines"] for source in web)
    assert searchers[0].query_log[0]["service_lines"] == CROSS
    assert all(entry["service_lines"] for entry in searchers[0].fetch_log)
