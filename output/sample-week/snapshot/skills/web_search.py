"""Web-search skill: Tavily raw page evidence first, then ddgs with fetched page text."""

import hashlib

from content_pipeline.models import SourceRecord
from content_pipeline.research import fetch_page, public_url


class WebSearch:
    """One search step: Tavily evidence first, then ddgs results with fetched page text."""

    def __init__(
        self, tavily=None, ddgs=None, fetch=fetch_page, check_url=public_url, evidence_limit=12000
    ):
        self.tavily = tavily
        self.ddgs = ddgs
        self.fetch = fetch
        self.check_url = check_url
        self.evidence_limit = evidence_limit
        self.calls = 0
        self.tavily_credits = 0
        self.tavily_calls = 0
        self.ddgs_calls = 0
        self.fetch_calls = 0
        self.tavily_credits_estimated = False

    def search(self, query: str):
        """Run one search step and return the retained source records."""
        if self.calls >= 4:
            raise ValueError("research search-step limit")

        self.calls += 1
        hits = []
        provider = "ddgs"

        if self.tavily:
            self.tavily_calls += 1

            try:
                response = self.tavily.search(
                    query=query, include_raw_content=True, include_usage=True, max_results=5
                )

                credits = (response.get("usage") or {}).get("credits")

                self.tavily_credits += credits if credits is not None else 1
                self.tavily_credits_estimated |= credits is None

                hits = response.get("results", [])
                provider = "tavily"
            except Exception:
                self.tavily_credits += 1
                self.tavily_credits_estimated = True
                hits = []

        if not hits and self.ddgs:
            self.ddgs_calls += 1
            provider = "ddgs"
            hits = list(self.ddgs.text(query, max_results=5))

        records = []

        for hit in hits:
            url = hit.get("url") or hit.get("href")

            if not url:
                continue

            try:
                self.check_url(url)
            except (ValueError, OSError):
                continue

            raw = hit.get("raw_content") if provider == "tavily" else None
            status = "ok"
            evidence = raw

            if not evidence:
                self.fetch_calls += 1

                try:
                    evidence = self.fetch(url)

                    if not evidence:
                        status = "fetch_failed"
                except Exception:
                    status = "fetch_failed"
                    evidence = ""

            records.append(
                SourceRecord(
                    source_id="WEB-" + hashlib.sha256(url.encode()).hexdigest()[:16],
                    kind="web",
                    url=url,
                    title=hit.get("title", url),
                    evidence_text=(evidence or "")[: self.evidence_limit],
                    status=status,
                )
            )

        return list({r.source_id: r for r in records}.values())


def web_search(searcher: WebSearch, query: str) -> list[dict]:
    """Return the serialized source records for one web search step."""
    return [record.model_dump(mode="json") for record in searcher.search(query)]
