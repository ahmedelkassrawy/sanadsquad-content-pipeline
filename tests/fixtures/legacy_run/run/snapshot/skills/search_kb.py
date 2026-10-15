"""Knowledge-base search skill: BM25 retrieval over the KB entries."""

import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from content_pipeline.models import SourceRecord


def words(text):
    """Tokenize text for BM25 scoring."""
    return re.findall(r"\w+", text.lower())


class KnowledgeBase:
    """One KB file, ranked by BM25 over names, aliases and text."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))

        self.entries = data["entries"]
        ids = [e["id"] for e in self.entries]

        if len(set(ids)) != len(ids):
            raise ValueError("duplicate KB ids")

        for entry in self.entries:
            for field in (
                "id",
                "name",
                "severity",
                "category",
                "description",
                "root_cause",
                "aliases",
                "references",
            ):
                if not entry.get(field):
                    raise ValueError("incomplete KB entry: " + field)

        self.index = BM25Okapi(
            [
                words(" ".join([e["name"], e["category"], e["description"], *e["aliases"]]))
                for e in self.entries
            ]
        )

    def search(self, query: str, top_k: int = 5):
        """Return the best-ranked entries for a query, most relevant first."""
        scores = self.index.get_scores(words(query))
        ranked = sorted(enumerate(self.entries), key=lambda pair: (-scores[pair[0]], pair[0]))

        return [e for _, e in ranked[:top_k]]

    def source(self, topic_id):
        """Build the `SourceRecord` for one KB entry."""
        entry = next(e for e in self.entries if e["id"] == topic_id)

        return SourceRecord(
            source_id=entry["id"],
            kind="kb",
            title=entry["name"],
            url=entry["references"][0],
            evidence_text=entry["description"] + "\nRoot cause: " + entry["root_cause"],
        )


def search_kb(kb: KnowledgeBase, query: str) -> list[dict]:
    """Return the top five knowledge-base entries for a query."""
    return kb.search(query, 5)
