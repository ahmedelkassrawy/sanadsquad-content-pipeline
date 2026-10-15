from pathlib import Path
from langchain_core.tools import StructuredTool
from .research import KnowledgeBase, WebSearch
from .config import local_file


def research_tools(root: Path, kb: KnowledgeBase, searcher: WebSearch):
    def search_kb(query: str) -> list[dict]:
        return kb.search(query, 5)

    def web_search(query: str) -> list[dict]:
        return [record.model_dump(mode="json") for record in searcher.search(query)]

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
