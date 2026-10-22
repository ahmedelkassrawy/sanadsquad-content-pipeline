import json
from pathlib import Path

import pytest

from content_pipeline.config import load_sources
from content_pipeline.models import ResearchPack, SourceRecord
from skills.brand_rules import check_brand
from skills.search_kb import KnowledgeBase
from skills.web_search import FixtureSearch, WebSearch


ROOT = Path(__file__).resolve().parents[1]


def test_new_kb_schema_builds_ss_source_with_required_evidence():
    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    entry = kb.entries[0]
    source = kb.source(entry["id"])

    assert source.source_id == entry["id"]
    assert source.kind == "kb"
    assert source.title == entry["title"]
    assert entry["content"]["description"] in source.evidence_text
    assert entry["content"]["recommendation"] in source.evidence_text


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
    assert search.fetch_log == ["https://docs.example.org/guide"]
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
