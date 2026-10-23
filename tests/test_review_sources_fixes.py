"""Regressions from the company-sources review."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from content_pipeline.config import Settings, load_angles, load_type
from content_pipeline.models import BrandRules, Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.pipeline import Pipeline, RunContext, snapshot
from content_pipeline.validation import validate
from skills.brand_rules import check_research_separation
from skills.web_search import WebSearch


ROOT = Path(__file__).resolve().parents[1]


def _rules():
    return BrandRules(
        brand_name="Sentinel Grove",
        banned_words=[],
        banned_characters=[],
        allowed_links=["docs.example.org", "github.com"],
        safe_fixes=[],
        approved_stats={},
        palette=["#111111", "#222222", "#333333"],
    )


def _web(source_id="WEB-abcdef", url="https://docs.example.org/fetched"):
    return SourceRecord(
        source_id=source_id,
        kind="web",
        title="Fetched page",
        url=url,
        evidence_text="Fetched evidence.",
    )


def test_b2_validate_rejects_links_not_fetched_as_web_sources():
    """KB locations are not fetched URLs, even when their host is otherwise allowed."""
    pack = ResearchPack(
        slot_id="thread",
        topic_id="SS-01",
        topic_name="Topic",
        sources=[
            _web(),
            SourceRecord(
                source_id="SS-01",
                kind="kb",
                title="KB entry",
                url="https://github.com/example/location",
                evidence_text="KB evidence.",
            ),
        ],
    )
    draft = Draft(
        slot_id="thread",
        tweets=[
            Tweet(
                text="Read https://github.com/example/location and https://docs.example.org/fetched.",
                source_ids=["WEB-abcdef"],
            )
        ]
        + [Tweet(text="Supported technical detail.", source_ids=["WEB-abcdef"]) for _ in range(4)],
    )

    result = validate(draft, pack, load_type(ROOT, "tech-thread"), _rules())

    assert "tweet_1:link_not_fetched:https://github.com/example/location" in result.violations


def test_b3_requires_thread_citations_and_vulnerability_kb_evidence():
    thread_pack = ResearchPack(
        slot_id="thread",
        topic_id="SS-01",
        topic_name="Topic",
        sources=[SourceRecord(source_id="SS-01", kind="kb", title="KB", evidence_text="Evidence.")],
    )
    thread = Draft(slot_id="thread", tweets=[Tweet(text="Detail.") for _ in range(5)])
    marketing_pack = ResearchPack(
        slot_id="marketing",
        topic_id="ANGLE-vulnerability",
        topic_name="Vulnerability",
        sources=[_web()],
        kb_allowed=True,
    )
    marketing = Draft(
        slot_id="marketing",
        tweets=[Tweet(text="Read https://docs.example.org/fetched.", source_ids=["WEB-abcdef"])],
    )

    assert "thread_citation_required" in check_research_separation(
        thread, thread_pack, load_type(ROOT, "tech-thread")
    )
    assert "marketing_kb_source_required" in check_research_separation(
        marketing, marketing_pack, load_type(ROOT, "marketing-post")
    )


def test_b4_redirect_target_must_obey_the_source_policy(monkeypatch):
    import http.client
    import content_pipeline.research as research

    monkeypatch.setattr(research, "public_url", lambda url: url)
    monkeypatch.setattr(
        research.socket,
        "getaddrinfo",
        lambda host, port, **_: [(None, None, None, None, ("93.184.216.34", port))],
    )
    monkeypatch.setattr(research.socket, "create_connection", lambda *_, **__: object())

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return SimpleNamespace(status=302, getheader=lambda _: "https://evil.example/redirect")

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)

    with pytest.raises(ValueError, match="source policy"):
        research.fetch_page(
            "http://docs.example.org/start",
            allow_url=lambda url: "docs.example.org" in url,
        )


def test_b5_search_uses_snapshot_configuration_and_live_credential_root(tmp_path):
    live_root = ROOT
    (live_root / "inputs/search-fixtures").mkdir(exist_ok=True)
    fixture = live_root / "inputs/search-fixtures/review-snapshot-fixture.json"
    fixture.write_text("[]", encoding="utf-8")
    try:
        (tmp_path / "run").mkdir()
        snapshot_root = snapshot(live_root, tmp_path / "run", [], Settings())
        credential_root = tmp_path / "credentials"
        seen = []

        def search_factory(config_root, credentials):
            seen.append((config_root, credentials))
            return WebSearch()

        context = RunContext(
            snapshot_root, credential_root, tmp_path / "run", Settings(), None, None, asyncio.Lock()
        )
        pipeline = Pipeline(context, [], search_factory=search_factory)

        pipeline._new_searcher()

        assert seen == [(snapshot_root, credential_root)]
        assert (snapshot_root / "inputs/search-fixtures/review-snapshot-fixture.json").read_text(
            encoding="utf-8"
        ) == "[]"
    finally:
        fixture.unlink(missing_ok=True)


def test_s2_logs_every_backend_attempt_and_filtered_returned_url():
    class Tavily:
        def search(self, **_kwargs):
            raise RuntimeError("offline")

    class DDGS:
        def text(self, *_args, **_kwargs):
            return [
                {"href": "https://evil.example/post", "title": "Denied"},
                {"href": "https://docs.example.org/guide", "title": "Allowed"},
            ]

    search = WebSearch(
        tavily=Tavily(),
        ddgs=DDGS(),
        allowed_domains={"docs.example.org"},
        denied_domains={"evil.example"},
        check_url=lambda url: url,
        fetch=lambda url: "Fetched " + url,
    )

    assert [record.url for record in search.search("topic")] == ["https://docs.example.org/guide"]
    assert search.query_log[0] == {
        "query": "topic",
        "service_line": None,
        "backend": "tavily",
        "result_count": 0,
        "failure": "RuntimeError",
    }
    assert search.query_log[1]["backend"] == "ddgs"
    assert {entry["reason"] for entry in search.url_log} >= {"policy_rejected", "fetched"}


def test_s3_load_angles_accepts_legacy_entries_snapshot_for_marketing(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "post_types/marketing-post").mkdir(parents=True)
    (tmp_path / "inputs/kb.json").write_text(
        '{"entries": [{"id": "KB-01", "name": "Legacy", "severity": "high", '
        '"category": ["legacy"], "description": "Description", "root_cause": "Cause", '
        '"aliases": ["legacy"]}]}',
        encoding="utf-8",
    )
    (tmp_path / "post_types/marketing-post/angles.yaml").write_text(
        yaml.safe_dump(
            {"angles": [{"id": "ANGLE-legacy", "name": "Legacy", "source_ids": ["KB-01"]}]}
        ),
        encoding="utf-8",
    )

    angles = load_angles(tmp_path, load_type(ROOT, "marketing-post"))

    assert [angle.id for angle in angles] == ["ANGLE-legacy"]
