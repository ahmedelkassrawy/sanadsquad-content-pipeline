import json
import re
from pathlib import Path

import pytest

from content_pipeline.config import load_sources
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.redaction import name_variants, redact_kb_text
from content_pipeline.tools import configured_web_search, research_tools
from content_pipeline.validation import validate
from skills.brand_rules import check_brand
from skills.search_kb import KnowledgeBase
from skills.web_search import FixtureSearch, WebSearch


ROOT = Path(__file__).resolve().parents[1]


def test_new_kb_schema_builds_ss_source_with_required_evidence():
    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    def normalize(value):
        return re.sub(r"[\s_.-]+", "", value).casefold()

    for entry in kb.entries:
        source = kb.source(entry["id"])

        assert source.source_id == entry["id"]
        assert source.kind == "kb"
        assert source.url is None
        assert source.title
        assert source.evidence_text
        writer_visible = normalize(source.title + "\n" + source.evidence_text)
        for name in name_variants(entry["protocol"]):
            assert normalize(name) not in writer_visible
        assert (
            redact_kb_text(entry["content"]["description"], kb.redacted_names)
            in source.evidence_text
        )
        assert (
            redact_kb_text(entry["content"]["recommendation"], kb.redacted_names)
            in source.evidence_text
        )


def test_source_record_accepts_ss_ids_for_kb():
    assert (
        SourceRecord(
            source_id="SS-03699", kind="kb", title="Finding", evidence_text="Evidence"
        ).source_id
        == "SS-03699"
    )


def test_source_registry_requires_known_service_and_valid_domains(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                service: {
                    group: ["docs.example.org"]
                    for group in ["blogs", "docs", "production", "analysis", "figures"]
                }
                for service in [
                    "web3-security",
                    "pentesting",
                    "agentic-security",
                    "agentic-payments",
                ]
            }
            | {"_deny": ["spam.example"]}
        ),
        encoding="utf-8",
    )

    registry = load_sources(path)
    assert registry.domains_for("web3-security") == {"docs.example.org"}
    with pytest.raises(ValueError, match="unknown service"):
        registry.domains_for("unknown")


def test_fixture_search_filters_hosts_and_logs_zero_result_query(tmp_path):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "prompt-injection.json").write_text(
        json.dumps(
            [
                {"url": "https://docs.example.org/guide", "title": "Allowed"},
                {"url": "https://evil.example/post", "title": "Denied"},
            ]
        ),
        encoding="utf-8",
    )
    search = WebSearch(
        fixture=FixtureSearch(fixture_dir),
        allowed_domains={"docs.example.org"},
        denied_domains={"evil.example"},
        fetch=lambda url: "Fetched " + url,
        check_url=lambda url: url,
    )

    results = search.search("prompt injection")
    assert [result.url for result in results] == ["https://docs.example.org/guide"]
    assert search.query_log == [
        {"query": "prompt injection", "service_line": None, "backend": "fixture", "result_count": 1}
    ]
    assert search.fetch_log == [{"url": "https://docs.example.org/guide", "service_line": None}]
    assert search.search("nothing here") == []
    assert search.query_log[-1]["result_count"] == 0


def test_post_links_must_have_been_fetched_in_this_run():
    pack = ResearchPack(
        slot_id="a",
        topic_id="WEB-abcd",
        topic_name="Topic",
        sources=[
            SourceRecord(
                source_id="WEB-abcd",
                kind="web",
                title="Topic",
                url="https://docs.example.org/guide",
                evidence_text="Fetched evidence",
            )
        ],
    )
    rules = type("Rules", (), {"banned_words": [], "banned_characters": [], "allowed_links": []})()

    assert check_brand("Read https://docs.example.org/guide", rules, pack) == []
    assert check_brand("Read https://docs.example.org/other", rules, pack) == [
        "link_not_fetched:https://docs.example.org/other"
    ]


def test_marketing_tools_exclude_kb_and_reject_kb_pack(tmp_path):
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    search = WebSearch(fixture=FixtureSearch(tmp_path))
    assert [tool.name for tool in research_tools(ROOT, kb, search, include_kb=False)] == [
        "web_search"
    ]
    pack = ResearchPack(
        slot_id="m",
        topic_id="ANGLE-service",
        topic_name="service",
        sources=[kb.source(kb.entries[0]["id"])],
    )
    draft = Draft(slot_id="m", tweets=[Tweet(text="Fact", source_ids=[pack.sources[0].source_id])])
    spec = __import__("content_pipeline.config", fromlist=["load_type"]).load_type(
        ROOT, "marketing-post"
    )
    rules = type(
        "Rules",
        (),
        {
            "banned_words": [],
            "banned_characters": [],
            "allowed_links": [],
            "safe_fixes": [],
            "brand_name": "x",
        },
    )()
    assert "marketing_kb_forbidden" in validate(draft, pack, spec, rules).violations


def test_vulnerability_marketing_requires_explicit_kb_citation():
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    source = kb.source(kb.entries[0]["id"])
    pack = ResearchPack(
        slot_id="m",
        topic_id="ANGLE-vulnerability",
        topic_name="vulnerability-class",
        sources=[source],
        kb_allowed=True,
    )
    draft = Draft(slot_id="m", tweets=[Tweet(text="Fact", source_ids=[])])
    spec = __import__("content_pipeline.config", fromlist=["load_type"]).load_type(
        ROOT, "marketing-post"
    )
    rules = type(
        "Rules",
        (),
        {
            "banned_words": [],
            "banned_characters": [],
            "allowed_links": [],
            "safe_fixes": [],
            "brand_name": "x",
        },
    )()
    assert "marketing_kb_citation_required" in validate(draft, pack, spec, rules).violations


def test_web_thread_requires_fetched_link():
    pack = ResearchPack(
        slot_id="t",
        topic_id="WEB-aabb",
        topic_name="External",
        sources=[
            SourceRecord(
                source_id="WEB-aabb",
                kind="web",
                title="External",
                url="https://docs.example.org/x",
                evidence_text="facts",
            )
        ],
    )
    draft = Draft(slot_id="t", tweets=[Tweet(text="Facts", source_ids=["WEB-aabb"])])
    spec = __import__("content_pipeline.config", fromlist=["load_type"]).load_type(
        ROOT, "tech-thread"
    )
    rules = type(
        "Rules",
        (),
        {
            "banned_words": [],
            "banned_characters": [],
            "allowed_links": [],
            "safe_fixes": [],
            "brand_name": "x",
        },
    )()
    assert (
        "thread_web_link_required:https://docs.example.org/x"
        in validate(draft, pack, spec, rules).violations
    )


def test_configured_search_without_key_uses_live_ddgs_not_fixtures(monkeypatch, tmp_path):
    import content_pipeline.tools as tools

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_BACKEND", raising=False)
    source_path = tmp_path / "sources.json"
    source_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tools, "local_file", lambda *_: source_path)
    monkeypatch.setattr(tools, "load_sources", lambda _: type("R", (), {"deny": []})())
    search = configured_web_search(tmp_path)
    assert search.tavily is None and search.ddgs is not None and search.fixture is None


def test_vulnerability_marketing_reports_both_missing_citations_at_once():
    """Live run: the writer fixed one missing citation per retry and dropped the other."""
    from skills.brand_rules import check_research_separation

    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    web = SourceRecord(
        source_id="WEB-abcdef12",
        kind="web",
        url="https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html",
        title="OWASP AI agent security",
        evidence_text="Enforce tool authorization outside the model.",
    )
    pack = ResearchPack(
        slot_id="m",
        topic_id="ANGLE-vulnerability-class",
        topic_name="vulnerability-class",
        sources=[web, kb.source(kb.entries[0]["id"])],
        kb_allowed=True,
    )
    spec = __import__("content_pipeline.config", fromlist=["load_type"]).load_type(
        ROOT, "marketing-post"
    )
    draft = Draft(slot_id="m", tweets=[Tweet(text="Fact", source_ids=[])])

    assert check_research_separation(draft, pack, spec) == [
        "marketing_kb_citation_required",
        "marketing_web_citation_required",
    ]
