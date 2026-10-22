from pathlib import Path
import os
from langchain_core.tools import StructuredTool
from skills import search_kb as search_kb_skill
from skills import web_search as web_search_skill
from skills.search_kb import KnowledgeBase
from skills.web_search import WebSearch
from .config import load_sources, local_file


def research_tools(root: Path, kb: KnowledgeBase, searcher: WebSearch, *, include_kb=True):
    """Expose the research skills as LLM-callable tools, described by their `.md` text."""

    def search_kb(query: str) -> list[dict]:
        """Return the top five knowledge-base entries for a query."""
        return search_kb_skill.search_kb(kb, query)

    def web_search(query: str) -> list[dict]:
        """Return the source records one web search step retained for a query."""
        return web_search_skill.web_search(searcher, query)

    tools = []
    if include_kb:
        tools.append(
            StructuredTool.from_function(
                search_kb,
                description=local_file(root / "skills", "search_kb.md").read_text(encoding="utf-8"),
            )
        )
    tools.append(
        StructuredTool.from_function(
            web_search,
            description=local_file(root / "skills", "web_search.md").read_text(encoding="utf-8"),
        )
    )
    return tools


def configured_web_search(root: Path):
    from skills.web_search import FixtureSearch

    registry = load_sources(local_file(root, "inputs/sources.json"))
    key = os.environ.get("TAVILY_API_KEY")
    # Offline is the safe default.  A missing API key must not silently fall through to DDGS,
    # because that turns ordinary local and test runs into network calls.
    if os.environ.get("SEARCH_BACKEND", "").lower() == "fixture" or not key:
        search = WebSearch(
            fixture=FixtureSearch(root / "inputs/search-fixtures"), denied_domains=registry.deny
        )
        search.registry = registry
        return search

    from ddgs import DDGS
    from tavily import TavilyClient

    search = WebSearch(
        tavily=TavilyClient(api_key=key) if key else None, ddgs=DDGS(), denied_domains=registry.deny
    )
    search.registry = registry
    return search
