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
    """One SanadSquad finding file, ranked by title, tags and evidence text."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))

        self.entries = data["findings"]
        ids = [e["id"] for e in self.entries]

        if len(set(ids)) != len(ids):
            raise ValueError("duplicate KB ids")

        for entry in self.entries:
            for field in ("id", "title", "impact", "tags", "protocol", "language", "content"):
                if entry.get(field) in (None, "", []):
                    raise ValueError("incomplete KB entry: " + field)
            for field in ("description", "location", "recommendation", "status"):
                if not entry["content"].get(field):
                    raise ValueError("incomplete KB entry content: " + field)
            if not re.fullmatch(r"SS-\d+", entry["id"]):
                raise ValueError("invalid KB id")
            # Compatibility aliases keep topic-selection code independent of the source schema.
            entry["name"] = entry["title"]
            entry["severity"] = entry["impact"]
            entry["aliases"] = entry["tags"]
            entry["description"] = entry["content"]["description"]

        self.index = BM25Okapi(
            [
                words(
                    " ".join(
                        [
                            e["title"],
                            *e["tags"],
                            *e["category"],
                            e["content"]["description"],
                            e["content"]["recommendation"],
                        ]
                    )
                )
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
            title=entry["title"],
            url=entry["content"]["location"],
            evidence_text="\n".join(
                (
                    entry["title"],
                    entry["content"]["description"],
                    entry["content"]["recommendation"],
                )
            ),
        )


def search_kb(kb: KnowledgeBase, query: str) -> list[dict]:
    """Return the top five knowledge-base entries for a query."""
    return kb.search(query, 5)
