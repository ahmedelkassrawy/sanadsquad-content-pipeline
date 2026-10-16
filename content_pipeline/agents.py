import asyncio
from pathlib import Path
from pydantic import ValidationError
from . import record_ids
from .config import local_file
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
from .tools import research_tools
from .review_scoring import (
    canonical_review,
    review_contract_errors,
    review_wire_schema,
    score_review,
)


__all__ = ["load_rubric"]


def identity(slot_id, attempt, output):
    if output.slot_id != slot_id or output.attempt != attempt:
        raise ModelFailure("failed_output")


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
        self.tools = {tool.name: tool for tool in research_tools(root, kb, searcher)}
        self.records = []
        self.usage_callback = usage_callback

    async def __call__(self, request: ResearchRequest) -> ResearchPack:
        slot = request.slot
        assigned = self.types.assigned_pack(slot, self.kb)

        if assigned is not None:
            return assigned

        evidence = {}
        searches = 0

        async def search(name, query):
            nonlocal searches

            if searches >= 4:
                raise ModelFailure("failed_research")

            searches += 1

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
                )
            )

        await search("search_kb", slot.focus or request.hints)

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
                    raise ModelFailure("failed_research")

                selected = [evidence[id] for id in dict.fromkeys(action.source_ids)]
                topic = action.topic_id or selected[0].source_id

                if action.topic_id and action.topic_id not in action.source_ids:
                    raise ModelFailure("failed_research")

                title = (
                    self.kb.source(topic).title
                    if topic.startswith("KB-")
                    else slot.focus or selected[0].title
                )

                return ResearchPack(
                    slot_id=slot.slot_id, topic_id=topic, topic_name=title, sources=selected
                )

            if not action.query.strip():
                raise ModelFailure("failed_research")

            await search(action.action, action.query)

        raise ModelFailure("failed_research")


class TopicSelector(Agent):
    def __init__(self, root, client, types=None):
        super().__init__(root, "research", client, types=types)

    async def __call__(self, request: TopicChoiceRequest) -> TopicChoices:
        instructions = local_file(self.root / "agents/research", "topics.md").read_text(
            encoding="utf-8"
        )

        output = await self.invoke(
            TopicChoices,
            request,
            request.slot_id,
            request.attempt,
            node="research_topics",
            instructions=instructions,
        )

        identity(request.slot_id, request.attempt, output)

        return output


class Writer(Agent):
    def __init__(self, root, post_type, client, types=None):
        types = types or PostTypes(root)

        super().__init__(root, types.writer(post_type), client, post_type, types)

    async def __call__(self, request: WriteRequest) -> Draft:
        if request.research.slot_id != request.slot_id:
            raise ModelFailure("failed_output")

        output = await self.invoke(Draft, request, request.slot_id, request.attempt, node="writer")

        identity(request.slot_id, request.attempt, output)

        return output


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
