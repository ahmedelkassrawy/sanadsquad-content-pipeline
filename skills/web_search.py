"""Allowlisted web search with a deterministic fixture backend for offline runs."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from content_pipeline.models import SourceRecord
from content_pipeline.research import fetch_page, public_url


class WebSearch:
    """One search step: Tavily evidence first, then ddgs results with fetched page text."""

    def __init__(
        self,
        tavily=None,
        ddgs=None,
        fixture=None,
        fetch=fetch_page,
        check_url=public_url,
        evidence_limit=12000,
        allowed_domains=None,
        denied_domains=None,
        service_line=None,
    ):
        self.tavily = tavily
        self.ddgs = ddgs
        self.fixture = fixture
        self.fetch = fetch
        self.check_url = check_url
        self.evidence_limit = evidence_limit
        self.calls = 0
        self.tavily_credits = 0
        self.tavily_calls = 0
        self.ddgs_calls = 0
        self.fetch_calls = 0
        self.allowed_domains = set(allowed_domains or ())
        self.denied_domains = set(denied_domains or ())
        self.service_line = service_line
        self.query_log = []
        self.fetch_log = []
        self.tavily_credits_estimated = False

    def _permitted(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        allowed = not self.allowed_domains or any(
            host == domain or host.endswith("." + domain) for domain in self.allowed_domains
        )
        denied = any(
            host == domain or host.endswith("." + domain) for domain in self.denied_domains
        )
        return allowed and not denied

    def search(self, query: str, service_line=None):
        """Run one search step and return the retained source records."""
        if self.calls >= 4:
            raise ValueError("research search-step limit")

        self.calls += 1
        hits = []
        provider = "fixture" if self.fixture else "ddgs"

        if self.fixture:
            hits = self.fixture.text(query)
        elif self.tavily:
            self.tavily_calls += 1

            try:
                response = self.tavily.search(
                    query=query,
                    include_raw_content=True,
                    include_usage=True,
                    max_results=5,
                    include_domains=sorted(self.allowed_domains) or None,
                    exclude_domains=sorted(self.denied_domains) or None,
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
            sites = " OR ".join("site:" + domain for domain in sorted(self.allowed_domains))
            hits = list(self.ddgs.text(f"({sites}) {query}" if sites else query, max_results=5))

        records = []

        for hit in hits:
            url = hit.get("url") or hit.get("href")

            if not url:
                continue

            if not self._permitted(url):
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
                self.fetch_log.append(url)

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

        records = list({r.source_id: r for r in records}.values())
        self.query_log.append(
            {
                "query": query,
                "service_line": service_line or self.service_line,
                "backend": provider,
                "result_count": len(records),
            }
        )
        return records


class FixtureSearch:
    """Small JSON fixture search used when no search API key is configured."""

    def __init__(self, directory: Path):
        self.directory = directory

    def text(self, query: str):
        name = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") + ".json"
        path = self.directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def web_search(searcher: WebSearch, query: str) -> list[dict]:
    """Return the serialized source records for one web search step."""
    return [record.model_dump(mode="json") for record in searcher.search(query)]
