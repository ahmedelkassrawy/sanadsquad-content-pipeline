import asyncio
import re
from pathlib import Path
from pydantic import ValidationError
from . import record_ids
from .config import load_sources, local_file
from .contracts import (
    ResearchAction,
    ResearchRecord,
    ResearchRequest,
    ResearchTurn,
    ReviewRequest,
    TopicChoiceRequest,
    TopicChoices,
    VisualOutput,
    VisualRequest,
    WriteRequest,
)
from .models import Draft, ResearchPack, Review, SourceRecord
from .model_client import ModelFailure
from .post_types import PostTypes, load_rubric
from .research import canonical_topic_choices, topic_wire_schema
from .tools import research_tools
from .review_scoring import (
    canonical_review,
    review_contract_errors,
    review_wire_schema,
    score_review,
)


__all__ = ["load_rubric"]


# Words too general to count toward a focus at all.
_FOCUS_STOPWORDS = frozenset(
    "the and for with from that this into over against attack attacks exploit exploits "
    "vulnerability vulnerabilities bug bugs smart contract contracts issue issues".split()
)
# Words that appear in several KB labels without naming a topic ("value" is in "return value" and
# "block values"). A focus must share at least one other word with an entry to match it.
_FOCUS_GENERIC_WORDS = (
    "value missing lack proper incorrect weak failed unchecked unprotected dependence proxy call"
)
# At least this share of a focus's words must appear in an entry's label.
FOCUS_MIN_OVERLAP = 0.5


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


_FOCUS_GENERIC = frozenset(_stem(word) for word in _FOCUS_GENERIC_WORDS.split())


def _focus_words(text: str) -> list[str]:
    """Content words of a focus or label; "re-entrancy" and "reentrancy" are the same word."""
    joined = re.sub(r"(?<=[a-z0-9])-(?=[a-z0-9])", "", text.casefold())

    return [
        _stem(word)
        for word in re.findall(r"[a-z0-9]+", joined)
        if len(word) >= 3 and word not in _FOCUS_STOPWORDS
    ]


def _label_words(entry: dict) -> set[str]:
    """Words of a KB entry's name, category and aliases, with "front running" also joined."""
    words = set()

    category = entry.get("category", "")
    categories = category if isinstance(category, list) else [category]
    for part in [entry["name"], *categories, *entry.get("aliases", [])]:
        if not isinstance(part, str):
            continue
        joined = re.sub(r"(?<=[a-z0-9])-(?=[a-z0-9])", "", part.casefold())
        pieces = re.findall(r"[a-z0-9]+", joined)

        words.update(_focus_words(part))
        # Preserve both forms of a hyphenated KB tag: "front-running" must
        # match a focus written as either "front-running" or "front running".
        words.update(_focus_words(part.replace("-", " ")))
        words.update(_stem(a + b) for a, b in zip(pieces, pieces[1:]))

    return words


def focus_matches(focus: str, kb, topic_id: str) -> bool:
    """Whether a KB entry's name, aliases or category cover the requested focus.

    At least half of the focus's words must appear in the entry's label, and one of them must be
    a distinctive word, so "unchecked return value" cannot match "Block values as time proxy"
    through "value". A focus with no meaningful words (a single letter) matches anything. Only the
    entry's label counts, not its description, so "re-pricing" cannot match a flash-loan entry
    that mentions pricing in passing. A focus that matches no entry fails closed: research
    searches the web for it.
    """
    wanted = set(_focus_words(focus))

    if not wanted:
        return True

    entry = next((e for e in kb.entries if e["id"] == topic_id), None)
    if entry is None:
        return False
    overlap = wanted & _label_words(entry)

    return len(overlap) / len(wanted) >= FOCUS_MIN_OVERLAP and bool(overlap - _FOCUS_GENERIC)


def identity(slot_id, attempt, output):
    if output.slot_id != slot_id or output.attempt != attempt:
        raise ModelFailure("failed_output", "identity_mismatch")


class Agent:
    def __init__(
        self,
        root: Path,
        name: str,
        client,
        post_type: str | None = None,
        types: PostTypes | None = None,
    ):
        self.root = root
        self.name = name
        self.client = client
        self.types = types or PostTypes(root)
        self.config = self.types.agent(name)
        self.prompt = self.types.prompt(name, post_type)

    async def invoke(
        self, schema, payload, slot_id, attempt, node=None, instructions="", output_validator=None
    ):
        messages = [
            {"role": "system", "content": self.prompt + instructions},
            {"role": "user", "content": payload.model_dump_json()},
        ]

        return await self.client.call(
            schema,
            messages,
            slot_id,
            node or self.name,
            attempt,
            self.config.max_output_tokens,
            output_validator=output_validator,
        )


class ResearchAgent(Agent):
    def __init__(self, root, client, kb, searcher, usage_callback=None, types=None):
        super().__init__(root, "research", client, types=types)

        self.kb = kb
        self.searcher = searcher
        self.tools = {}
        self.records = []
        self.usage_callback = usage_callback

    async def __call__(self, request: ResearchRequest) -> ResearchPack:
        slot = request.slot
        marketing = slot.type == "marketing-post"
        angle = None
        if self.types.spec(slot.type).research.topic_source == "angles" and slot.topic_id:
            angle = next((a for a in self.types.angles(slot.type) if a.id == slot.topic_id), None)
        kb_allowed = bool(angle and (angle.kb_allowed or angle.subject == "vulnerability-class"))
        # Marketing always grounds its point in allowlisted web research. A vulnerability-class
        # angle receives its configured KB finding only after a web source has been selected.
        self.tools = {
            tool.name: tool
            for tool in research_tools(
                self.root,
                self.kb,
                self.searcher,
                include_kb=not marketing,
            )
        }
        if marketing:
            if not slot.service_line:
                raise ModelFailure("failed_research", "marketing_service_line_missing")
            if self.searcher.registry is None:
                sources = self.root / "inputs/sources.json"
                if sources.is_file():
                    self.searcher.registry = load_sources(sources)
            if self.searcher.registry is not None:
                self.searcher.allowed_domains = self.searcher.registry.domains_for(
                    slot.service_line
                )
            self.searcher.service_line = slot.service_line

        assigned = None if marketing else self.types.assigned_pack(slot, self.kb)

        if assigned is not None:
            if (
                slot.type == "marketing-post"
                and any(source.kind == "kb" for source in assigned.sources)
                and not kb_allowed
            ):
                raise ModelFailure("failed_research", "marketing_kb_forbidden")
            return assigned.model_copy(update={"kb_allowed": kb_allowed})

        if (
            slot.type == "tech-thread"
            and slot.focus
            and slot.focus.startswith(("http://", "https://"))
        ):
            url = slot.focus
            url_start = len(self.searcher.url_log)
            evidence, final_url, status = self.searcher.fetch_url(url)
            if status != "ok":
                self.records.append(
                    ResearchRecord(
                        record_id=record_ids.record_id(
                            slot.slot_id, "research_search", request.attempt, 1
                        ),
                        slot_id=slot.slot_id,
                        attempt=request.attempt,
                        evidence=[],
                        action="web_fetch",
                        query=url,
                        url_events=self.searcher.url_log[url_start:],
                        failure="external_fetch_failed",
                    )
                )
                raise ModelFailure("failed_research", "external_fetch_failed")
            record = SourceRecord(
                source_id="WEB-"
                + __import__("hashlib").sha256(final_url.encode()).hexdigest()[:16],
                kind="web",
                url=final_url,
                title=final_url,
                evidence_text=evidence,
            )
            self.records.append(
                ResearchRecord(
                    record_id=record_ids.record_id(
                        slot.slot_id, "research_search", request.attempt, 1
                    ),
                    slot_id=slot.slot_id,
                    attempt=request.attempt,
                    evidence=[record],
                    action="web_fetch",
                    query=url,
                    url_events=self.searcher.url_log[url_start:],
                )
            )
            return ResearchPack(
                slot_id=slot.slot_id,
                topic_id=record.source_id,
                topic_name=record.title,
                sources=[record],
            )

        evidence = {}
        searches = 0

        async def search(name, query):
            nonlocal searches

            if searches >= 4:
                raise ModelFailure("failed_research", "search_limit_reached:4_searches")

            searches += 1
            query_start = len(self.searcher.query_log)
            url_start = len(self.searcher.url_log)

            if name not in self.tools:
                self.records.append(
                    ResearchRecord(
                        record_id=record_ids.record_id(
                            slot.slot_id, "research_search", request.attempt, searches
                        ),
                        slot_id=slot.slot_id,
                        attempt=request.attempt,
                        evidence=[],
                        action=name,
                        query=query,
                        query_attempts=self.searcher.query_log[query_start:],
                        url_events=self.searcher.url_log[url_start:],
                        failure=f"tool_unavailable:{name}",
                    )
                )
                return

            try:
                data = await asyncio.to_thread(self.tools[name].invoke, {"query": query})
            finally:
                if self.usage_callback:
                    await self.usage_callback(self.searcher)

            records = (
                [self.kb.source(entry["id"]) for entry in data]
                if name == "search_kb"
                else [SourceRecord.model_validate(item) for item in data]
            )

            evidence.update({record.source_id: record for record in records})

            self.records.append(
                ResearchRecord(
                    record_id=record_ids.record_id(
                        slot.slot_id, "research_search", request.attempt, searches
                    ),
                    slot_id=slot.slot_id,
                    attempt=request.attempt,
                    evidence=records,
                    action=name,
                    query=query,
                    query_attempts=self.searcher.query_log[query_start:],
                    url_events=self.searcher.url_log[url_start:],
                )
            )

        if marketing:
            initial_query = slot.focus or (angle.seed_query if angle and angle.seed_query else "")
            initial_query = initial_query or (
                f"{angle.name} {slot.service_line}"
                if angle
                else slot.service_line or "security research"
            )
            initial_tool = "web_search"
        else:
            topic_name = (
                self.kb.source(slot.topic_id).title
                if slot.topic_id and any(entry["id"] == slot.topic_id for entry in self.kb.entries)
                else slot.topic_id
            )
            initial_query = slot.focus or topic_name or "technical security research"
            initial_tool = "search_kb" if "search_kb" in self.tools else "web_search"

        await search(initial_tool, initial_query)

        instructions = local_file(self.root / "agents/research", "loop.md").read_text(
            encoding="utf-8"
        )

        for turn in range(1, 6):
            action = await self.invoke(
                ResearchAction,
                ResearchTurn(
                    request=request,
                    evidence=list(evidence.values()),
                    searches_remaining=4 - searches,
                    available_actions=[*self.tools, "finish"],
                ),
                slot.slot_id,
                request.attempt,
                node=f"research_step_{turn}",
                instructions=instructions,
            )

            identity(slot.slot_id, request.attempt, action)

            if action.action == "finish":
                if not action.source_ids or any(
                    id not in evidence or evidence[id].status != "ok" for id in action.source_ids
                ):
                    raise ModelFailure("failed_research", "finish_cited_no_usable_source")

                selected = [evidence[id] for id in dict.fromkeys(action.source_ids)]
                topic = action.topic_id or selected[0].source_id

                if action.topic_id and action.topic_id not in action.source_ids:
                    raise ModelFailure("failed_research", "topic_not_among_cited_sources")

                if (
                    slot.focus
                    and topic.startswith("SS-")
                    and not focus_matches(slot.focus, self.kb, topic)
                ):
                    # The operator asked for one topic; shipping the nearest other one is a
                    # substitution, so the slot fails instead.
                    raise ModelFailure("failed_research", "topic_does_not_match_focus")

                title = (
                    self.kb.source(topic).title
                    if topic.startswith("SS-")
                    else slot.focus or selected[0].title
                )

                if marketing:
                    if not any(
                        source.kind == "web" and source.status == "ok" for source in selected
                    ):
                        raise ModelFailure("failed_research", "marketing_web_source_required")

                    additions = self.types.assigned_pack(
                        slot.model_copy(update={"focus": None}), self.kb
                    )
                    if additions is not None:
                        selected = list(
                            {
                                source.source_id: source
                                for source in [*selected, *additions.sources]
                            }.values()
                        )
                    if angle is not None:
                        topic = angle.id
                        title = angle.name

                return ResearchPack(
                    slot_id=slot.slot_id,
                    topic_id=topic,
                    topic_name=title,
                    sources=selected,
                    kb_allowed=kb_allowed,
                )

            if not action.query.strip():
                raise ModelFailure("failed_research", "empty_search_query")

            await search(action.action, action.query)

        raise ModelFailure("failed_research", "step_limit_reached_without_supporting_evidence")


class TopicSelector(Agent):
    def __init__(self, root, client, types=None):
        super().__init__(root, "research", client, types=types)

    async def __call__(self, request: TopicChoiceRequest) -> TopicChoices:
        instructions = local_file(self.root / "agents/research", "topics.md").read_text(
            encoding="utf-8"
        )

        output = await self.invoke(
            topic_wire_schema(request),
            request,
            request.slot_id,
            request.attempt,
            node="research_topics",
            instructions=instructions,
        )

        identity(request.slot_id, request.attempt, output)

        return canonical_topic_choices(output)


class Writer(Agent):
    def __init__(self, root, post_type, client, types=None):
        types = types or PostTypes(root)

        super().__init__(root, types.writer(post_type), client, post_type, types)

    async def __call__(self, request: WriteRequest) -> Draft:
        if request.research.slot_id != request.slot_id:
            raise ModelFailure("failed_output", "research_slot_mismatch")

        output = await self.invoke(Draft, request, request.slot_id, request.attempt, node="writer")

        if output.slot_id != request.slot_id:
            raise ModelFailure("failed_output", "slot_id_mismatch")

        # Draft.attempt defaults to 1, so a retry that omits or copies it is not a bad draft.
        return output.model_copy(update={"attempt": request.attempt})


class Reviewer(Agent):
    def __init__(self, root, post_type, client, types=None):
        super().__init__(root, "reviewer", client, post_type, types)

    async def __call__(self, request: ReviewRequest) -> Review:
        if (
            request.research.slot_id != request.slot_id
            or request.draft.slot_id != request.slot_id
            or request.draft.attempt != request.attempt
        ):
            raise ModelFailure("failed_output")

        def check(assessment):
            errors = review_contract_errors(request, canonical_review(assessment))

            if errors:
                raise ValidationError.from_exception_data("ReviewAssessment", errors)

        assessment = await self.invoke(
            review_wire_schema(request),
            request,
            request.slot_id,
            request.attempt,
            node="reviewer",
            output_validator=check,
        )

        return score_review(request, canonical_review(assessment))


class VisualAgent(Agent):
    def __init__(self, root, client, types=None):
        super().__init__(root, "visual", client, types=types)

    async def __call__(self, request: VisualRequest, feedback: list[str] | None = None):
        instructions = (
            "\nRepair the previous visual spec. Validation errors: " + ", ".join(feedback)
            if feedback
            else ""
        )

        output = await self.invoke(
            VisualOutput,
            request,
            request.slot_id,
            request.attempt,
            node="visual",
            instructions=instructions,
        )

        identity(request.slot_id, request.attempt, output)

        return output.spec
