"""The pipeline calls the skill modules: the brand validator, overlay check, research tools.

Each caller looks the skill function up on its module when it runs, so patching the skill changes
the caller's result without reloading any consumer. The distribution ships the skill modules too.
"""

import tomllib
from pathlib import Path

import skills.brand_rules
import skills.search_kb
import skills.web_search
from content_pipeline import images, research, validation
from content_pipeline.config import load_brand, load_type
from content_pipeline.contracts import VisualRequest
from content_pipeline.models import Draft, ImageSpec, ResearchPack, SourceRecord, Tweet
from content_pipeline.tools import research_tools

ROOT = Path(__file__).resolve().parents[1]


def brand_rules():
    return load_brand(ROOT / "inputs/brand-voice.md")[0]


def pack(slot_id="thread_1"):
    return ResearchPack(
        slot_id=slot_id,
        topic_id="KB-01",
        topic_name="Reentrancy",
        sources=[
            SourceRecord(
                source_id="KB-01",
                kind="kb",
                title="Reentrancy",
                evidence_text="External calls can reenter before state is updated.",
            )
        ],
    )


def draft(text="State before calls.", count=5, slot_id="thread_1"):
    return Draft(
        slot_id=slot_id,
        tweets=[Tweet(text=text, source_ids=["KB-01"]) for _ in range(count)],
    )


def visual_request(severity=None, slot_id="thread_1"):
    return VisualRequest(
        slot_id=slot_id,
        attempt=1,
        draft=draft(slot_id=slot_id),
        research=pack(slot_id=slot_id),
        post_type=load_type(ROOT, "tech-thread"),
        severity=severity,
    )


def image_spec(overlay_text="State before calls.", severity=None):
    return ImageSpec(
        concept="Reentrancy",
        scene="Reentrancy",
        palette=brand_rules().palette,
        overlay_text=overlay_text,
        alt_text=overlay_text,
        severity=severity,
    )


def test_brand_rules_skill_is_the_mechanical_brand_check():
    rules = brand_rules()
    banned = rules.banned_words[0]

    assert skills.brand_rules.check_brand("State before calls.", rules) == []
    assert skills.brand_rules.check_brand("#tag", rules) == ["hashtag"]
    assert skills.brand_rules.check_brand(banned, rules) == ["banned_phrase:" + banned]
    assert skills.brand_rules.check_brand(f"see {rules.allowed_links[0]}", rules) == []
    assert skills.brand_rules.check_brand("see https://evil.com/a", rules) == [
        "link_domain:evil.com"
    ]


def test_validator_calls_the_brand_rules_skill(monkeypatch):
    seen = []

    def fake_check_brand(text, rules):
        seen.append(text)
        return ["banned_phrase:synergy"]

    monkeypatch.setattr(skills.brand_rules, "check_brand", fake_check_brand)

    result = validation.validate(draft(), pack(), load_type(ROOT, "tech-thread"), brand_rules())

    assert result.accepted is False
    assert result.violations == [f"tweet_{i}:banned_phrase:synergy" for i in range(1, 6)]
    assert len(seen) == 5


def test_overlay_check_uses_the_brand_rules_skill_without_reload(monkeypatch):
    rules = brand_rules()
    request = visual_request()
    spec = image_spec()

    assert images.overlay_errors(spec, request, rules) == []

    monkeypatch.setattr(skills.brand_rules, "check_brand", lambda text, brand: ["review_marker"])

    assert images.overlay_errors(spec, request, rules) == ["review_marker"]


def test_overlay_check_uses_the_brand_rules_skill_for_the_severity_badge(monkeypatch):
    rules = brand_rules()
    request = visual_request(severity="High")
    spec = image_spec(severity="High")

    assert images.overlay_errors(spec, request, rules) == []

    def fake_check_brand(text, brand):
        return ["review_marker"] if text == "High" else []

    monkeypatch.setattr(skills.brand_rules, "check_brand", fake_check_brand)

    assert images.overlay_errors(spec, request, rules) == ["review_marker"]


def test_fallback_spec_uses_the_brand_rules_skill_without_reload(monkeypatch):
    rules = brand_rules()
    request = visual_request(severity="High")

    def fake_check_brand(text, brand):
        return ["review_marker"] if text == "High" else []

    monkeypatch.setattr(skills.brand_rules, "check_brand", fake_check_brand)

    spec = images.fallback_spec(request, rules)

    assert spec.overlay_text == request.research.topic_name
    assert spec.severity is None


def test_research_tools_call_the_skill_modules(monkeypatch):
    calls = []

    def fake_search_kb(kb, query):
        calls.append(("search_kb", query))
        return [{"id": "KB-42", "name": "Fake entry"}]

    def fake_web_search(searcher, query):
        calls.append(("web_search", query))
        return [{"source_id": "WEB-42", "kind": "web"}]

    monkeypatch.setattr(skills.search_kb, "search_kb", fake_search_kb)
    monkeypatch.setattr(skills.web_search, "web_search", fake_web_search)

    kb = skills.search_kb.KnowledgeBase(ROOT / "inputs/kb.json")
    tools = research_tools(ROOT, kb, skills.web_search.WebSearch())

    assert [tool.name for tool in tools] == ["search_kb", "web_search"]
    assert [tool.description for tool in tools] == [
        (ROOT / "skills/search_kb.md").read_text(encoding="utf-8").strip(),
        (ROOT / "skills/web_search.md").read_text(encoding="utf-8").strip(),
    ]
    assert tools[0].invoke({"query": "reentrancy"}) == [{"id": "KB-42", "name": "Fake entry"}]
    assert tools[1].invoke({"query": "oracle manipulation"}) == [
        {"source_id": "WEB-42", "kind": "web"}
    ]
    assert calls == [("search_kb", "reentrancy"), ("web_search", "oracle manipulation")]


def test_old_import_paths_are_re_exports_of_the_skill_modules():
    assert validation.check_brand is skills.brand_rules.check_brand
    assert research.KnowledgeBase is skills.search_kb.KnowledgeBase
    assert research.WebSearch is skills.web_search.WebSearch

    assert skills.brand_rules.check_brand.__module__ == "skills.brand_rules"
    assert skills.search_kb.search_kb.__module__ == "skills.search_kb"
    assert skills.search_kb.KnowledgeBase.__module__ == "skills.search_kb"
    assert skills.web_search.web_search.__module__ == "skills.web_search"
    assert skills.web_search.WebSearch.__module__ == "skills.web_search"


def test_skills_package_ships_in_the_distribution():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]

    assert "skills*" in include
    assert (ROOT / "skills/__init__.py").is_file()
    assert (ROOT / "skills/brand_rules.py").is_file()
    assert (ROOT / "skills/search_kb.py").is_file()
    assert (ROOT / "skills/web_search.py").is_file()
