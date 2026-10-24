"""Regressions found in the company live run."""

from pathlib import Path

from content_pipeline.agents import ResearchAgent
from content_pipeline.config import load_brand, load_type
from content_pipeline.contracts import ResearchRequest, ResearchTurn
from content_pipeline.models import Draft, ResearchPack, ResolvedSlot, SourceRecord, Tweet
from content_pipeline.validation import check_post_limits, length_feedback, validate
from skills.web_search import WebSearch


def web_source(number: int, evidence: str = "Evidence.") -> SourceRecord:
    return SourceRecord(
        source_id=f"WEB-{number:016x}",
        kind="web",
        url=f"https://example.com/{number}",
        title=f"Source {number}",
        evidence_text=evidence,
    )


def test_web_search_retains_at_most_the_configured_evidence_limit():
    page = "x" * 12_000

    class Tavily:
        def search(self, **_):
            return {
                "results": [
                    {"url": "https://example.com/page", "title": "Page", "raw_content": page}
                ]
            }

    source = WebSearch(tavily=Tavily()).search("page")[0]

    assert len(source.evidence_text) <= 3_000


def test_research_turn_caps_five_web_sources_below_20k_json_characters():
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.evidence_limit = 3_000
    sources = [web_source(number, "x" * 12_000) for number in range(5)]
    turn = ResearchTurn(
        request=ResearchRequest(
            slot=ResolvedSlot(slot_id="research", type="tech-thread"), hints="research"
        ),
        evidence=agent._turn_evidence(sources),
        searches_remaining=3,
        available_actions=["search_kb", "web_search", "finish"],
    )

    assert all(len(source.evidence_text) <= 3_000 for source in turn.evidence)
    assert len(turn.model_dump_json()) < 20_000


def test_two_service_statements_do_not_exceed_the_brand_mention_limit():
    root = Path(__file__).resolve().parents[1]
    rules, _ = load_brand(root / "inputs/brand-voice.md")
    spec = load_type(root, "marketing-post")
    text = " ".join((rules.approved_stats["STAT-04"], rules.approved_stats["STAT-05"]))
    pack = ResearchPack(
        slot_id="marketing",
        topic_id="ANGLE-agentic-payments",
        topic_name="agentic payments",
        sources=[
            web_source(11),
            SourceRecord(
                source_id="STAT-04",
                kind="stat",
                title=rules.brand_name,
                evidence_text=rules.approved_stats["STAT-04"],
            ),
            SourceRecord(
                source_id="STAT-05",
                kind="stat",
                title=rules.brand_name,
                evidence_text=rules.approved_stats["STAT-05"],
            ),
        ],
    )
    draft = Draft(
        slot_id="marketing",
        tweets=[Tweet(text=text, source_ids=["WEB-000000000000000b", "STAT-04", "STAT-05"])],
    )

    assert not [
        violation
        for violation in check_post_limits([draft.tweets[0].text], pack, spec, rules.brand_name)
        if violation.startswith("brand_mention_limit:")
    ]


def test_marketing_writer_restores_per_post_link_aware_word_budgets():
    root = Path(__file__).resolve().parents[1]
    writer = (root / "post_types/marketing-post/writer.md").read_text(encoding="utf-8")

    assert "max_tweet_chars / 7.5" in writer
    assert "(max_tweet_chars - 24) / 7.5" in writer
    assert "Cut a whole sentence" in writer


def test_marketing_3_retry_length_feedback_has_measurement_and_word_budget_fields():
    root = Path(__file__).resolve().parents[1]
    rules, _ = load_brand(root / "inputs/brand-voice.md")
    text = (
        "Nomad lost $190M in 2022. Zellic's coverage tracker showed only 18.6% of the on-chain "
        "Replica.sol was audited, and Quantstamp said the buggy code came in after its audit began. "
        "Root cause: deployed code drifted from audited code. Scope has to track what ships. "
        "https://www.zellic.io/blog/audit-drift"
    )
    draft = Draft(
        slot_id="marketing_3",
        tweets=[Tweet(text=text, source_ids=["WEB-0000000000000003"])],
    )
    pack = ResearchPack(
        slot_id="marketing_3",
        topic_id="ANGLE-web3-security",
        topic_name="web3-security",
        sources=[web_source(3, text)],
    )

    spec = load_type(root, "marketing-post")
    format = next(format for format in spec.formats if format.id == "news-reaction")
    feedback = length_feedback(validate(draft, pack, spec, rules, format=format))

    assert any(
        "weighted_length:" in note and ":words:" in note and ":max_words:" in note
        for note in feedback
    )
