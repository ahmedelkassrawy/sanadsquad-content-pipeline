from pathlib import Path
from langchain_core.tools import StructuredTool
from skills import search_kb as search_kb_skill
from skills import web_search as web_search_skill
from skills.search_kb import KnowledgeBase
from skills.web_search import WebSearch
from .config import local_file


def research_tools(root: Path, kb: KnowledgeBase, searcher: WebSearch):
    """Expose the research skills as LLM-callable tools, described by their `.md` text."""

    def search_kb(query: str) -> list[dict]:
        """Return the top five knowledge-base entries for a query."""
        return search_kb_skill.search_kb(kb, query)

    def web_search(query: str) -> list[dict]:
        """Return the source records one web search step retained for a query."""
        return web_search_skill.web_search(searcher, query)

    return [
        StructuredTool.from_function(
            search_kb,
            description=local_file(root / "skills", "search_kb.md").read_text(encoding="utf-8"),
        ),
        StructuredTool.from_function(
            web_search,
            description=local_file(root / "skills", "web_search.md").read_text(encoding="utf-8"),
        ),
    ]


def configured_web_search(root: Path):
    import os

    from dotenv import dotenv_values

    from ddgs import DDGS

    from tavily import TavilyClient

    key = os.environ.get("TAVILY_API_KEY") or dotenv_values(root / ".env").get("TAVILY_API_KEY")

    return WebSearch(tavily=TavilyClient(api_key=key) if key else None, ddgs=DDGS())
