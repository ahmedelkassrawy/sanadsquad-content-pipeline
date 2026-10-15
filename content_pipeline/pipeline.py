import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from . import record_ids
from .agents import ResearchAgent, TopicSelector, Writer, Reviewer, VisualAgent, load_rubric
from .config import (
    Settings,
    load_agent,
    load_angles,
    load_brand,
    load_type,
    local_file,
    resolve_model,
)
from .contracts import (
    DraftAudit,
    DraftRecord,
    Estimate,
    ExportPost,
    ExternalUsage,
    FinalPiece,
    ResearchRecord,
    ResearchRequest,
    ResolvedPlan,
    ReviewRecord,
    ReviewRequest,
    RunSummary,
    SlotState,
    TerminalSlot,
    TopicChoiceRequest,
    VisualRecord,
    VisualRequest,
    WeekSnapshot,
    WriteRequest,
)
from .images import fallback_spec, overlay_errors, render_card
from .ledger import BudgetExhausted, Ledger
from .model_client import ModelFailure, Prices, configured_client, valid_provider
from .models import ImageResult, ResolvedSlot, SlotOutcome
from .research import KnowledgeBase, assign_topics
from .tools import configured_web_search
from .trail import Trail, export_posts
from .validation import length_feedback, validate
from .advisor import Advisor, InvalidAdvice, advice_patch, instruction_files
from .contracts import AdvisorRecord, AdvisorRequest, RunIdentity
from .durability import RunLock, atomic_json, resume_inputs, snapshot_hashes


MAX_CONTENT_ATTEMPTS = 2
HISTORY_RUN_LIMIT = 4
ESTIMATED_INPUT_TOKENS = 16000
ESTIMATED_OUTPUT_MULTIPLIER = 1.5
ESTIMATED_CALL_ALLOWANCE = 8
CARD_SIZE = (1200, 675)
GRAPH_RECURSION_LIMIT = 50
REQUIRED_RUN_FILES = (
    "agents/research/loop.md",
    "agents/research/topics.md",
    "config/repair.md",
    "config/visual.yaml",
)


def safe_name(name):
    """Validate a slot, type or agent name for safe local filenames."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or re.fullmatch(
        r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name
    ):
        raise ValueError("unsafe slot/type/agent name")

    return name


def parse_sets(slots, values):
    """Copy slots and apply explicit focus overrides by slot ID."""
    indexed = {slot.slot_id: slot.model_copy(deep=True) for slot in slots}

    for value in values:
        key, separator, focus = value.partition("=")

        if not separator or key not in indexed or not focus.strip():
            raise ValueError("--set must name an existing slot and nonempty focus")

        indexed[key].focus = focus.strip()
        indexed[key].topic_id = None

    return list(indexed.values())


def validate_inputs(root, slots, settings=None):
    """Validate the plan and required configuration before starting a run."""
    if not slots or len({slot.slot_id.casefold() for slot in slots}) != len(slots):
        raise ValueError("plan needs case-insensitive unique slots")

    from PIL import ImageColor

    rules, _ = load_brand(root / "inputs/brand-voice.md")

    if len(rules.palette) < 3:
        raise ValueError("brand palette needs three colors")

    for color in rules.palette:
        ImageColor.getrgb(color)

    KnowledgeBase(root / "inputs/kb.json")

    for slot in slots:
        _validate_slot_inputs(root, slot, settings)

    for name in REQUIRED_RUN_FILES:
        local_file(root, name)


def _validate_slot_inputs(root, slot, settings):
    safe_name(slot.slot_id)
    safe_name(slot.type)

    spec = load_type(root, slot.type)

    if spec.research.topic_source == "angles":
        load_angles(root, spec)

    if "unsupported_claim" not in spec.review.hard_fail:
        raise ValueError("rubric must hard-fail unsupported claims")

    safe_name(spec.writer)

    load_rubric(root, spec)

    agent_names = [spec.writer, "research", "reviewer", "visual"]

    if not settings or settings.advisor_enabled:
        agent_names.append("advisor")

    for name in agent_names:
        _validate_agent_inputs(root, name, settings)


def _validate_agent_inputs(root, name, settings):
    agent = load_agent(root, name)

    local_file(root / "agents", name + "/system.md")

    for skill in agent.skills:
        local_file(root / "skills", skill + ".md")
        local_file(root / "skills", skill + ".py")

    if settings:
        model = resolve_model(settings, agent)

        provider, separator, reference = model.partition(":")

        if (
            not separator
            or not valid_provider(provider)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", reference)
            or (provider == "openrouter" and "/" not in reference)
        ):
            raise ValueError("invalid configured model reference")


def estimate(root, slots, settings):
    """Estimate worst-case token and cost budgets without initializing clients."""
    validate_inputs(root, slots, settings)

    tokens = 0
    cost = 0.0
    unpriced = False
    warnings = []
    prices = Prices(update=False)
    names = _estimate_agent_names(root, slots, settings)

    for name in names:
        config = load_agent(root, name)
        # Includes up to 4 search turns, two content attempts, parsing/truncation
        # allowances and three transient retries. Input is still an estimate.
        calls = _estimated_agent_calls(root, slots, name)
        output = int(config.max_output_tokens * ESTIMATED_OUTPUT_MULTIPLIER)
        reserved = (ESTIMATED_INPUT_TOKENS + output) * calls * ESTIMATED_CALL_ALLOWANCE
        tokens += reserved

        try:
            quoted, _ = prices.quote(
                resolve_model(settings, config), ESTIMATED_INPUT_TOKENS, output
            )

            cost += quoted * calls * ESTIMATED_CALL_ALLOWANCE
        except LookupError:
            unpriced = True

            warnings.append("unpriced_model:" + name)

    if unpriced:
        cost = None

    if tokens > settings.run_token_budget or (
        cost is not None and cost > settings.run_cost_budget_usd
    ):
        warnings.append("worst_case_exceeds_budget")

    return Estimate(
        slots=len(slots),
        worst_case_tokens=tokens,
        worst_case_cost_usd=cost,
        token_budget=settings.run_token_budget,
        cost_budget_usd=settings.run_cost_budget_usd,
        warnings=warnings,
    )


def _estimate_agent_names(root, slots, settings):
    names = ["research"]

    for slot in slots:
        writer = load_type(root, slot.type).writer

        names.extend(["research", writer, "reviewer", "visual"])

    if settings.advisor_enabled:
        names.append("advisor")

    return names


def _estimated_agent_calls(root, slots, name):
    if name == "research":
        return 5

    writers = {load_type(root, slot.type).writer for slot in slots}

    if name in writers | {"reviewer"}:
        return MAX_CONTENT_ATTEMPTS

    return 1


def history(parent):
    """Read topic IDs from the four most recently modified run summaries."""
    runs = sorted(
        (
            path
            for path in parent.iterdir()
            if path.is_dir() and (path / "run_summary.json").is_file()
        ),
        key=lambda path: path.stat().st_mtime,
    )

    result = []

    for path in runs[-HISTORY_RUN_LIMIT:]:
        try:
            summary = RunSummary.model_validate_json(
                (path / "run_summary.json").read_text(encoding="utf-8")
            )

            result.append([slot.topic_id for slot in summary.slots.values() if slot.topic_id])
        except (ValueError, OSError):
            continue

    return result


def snapshot(root, run_dir, slots, settings):
    """Copy all run inputs and resolved settings into an immutable snapshot."""
    target = run_dir / "snapshot"

    target.mkdir()

    for name in ["inputs/brand-voice.md", "inputs/kb.json"]:
        source = local_file(root, name)
        destination = target / name

        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(source, destination)

    files = _snapshot_files(root, slots)

    for name in sorted(files):
        source = local_file(root, name)
        destination = target / name

        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(source, destination)

    (target / "inputs/plan.json").write_text(
        json.dumps({"slots": [slot.model_dump(mode="json") for slot in slots]}, indent=2),
        encoding="utf-8",
    )

    (target / "resolved-config.json").write_text(
        settings.model_dump_json(indent=2), encoding="utf-8"
    )

    return target


def _snapshot_files(root, slots):
    files = set(REQUIRED_RUN_FILES)
    names = {"research", "reviewer", "visual", "advisor"}

    for slot in slots:
        spec = load_type(root, slot.type)

        names.add(spec.writer)

        for name in [
            "type.yaml",
            "writer.md",
            spec.rubric,
            spec.research.hints,
            spec.research.angles,
        ]:
            if name:
                files.add(f"post_types/{slot.type}/{name}")

    for name in names:
        files.update([f"agents/{name}/agent.yaml", f"agents/{name}/system.md"])

        for skill in load_agent(root, name).skills:
            files.update([f"skills/{skill}.md", f"skills/{skill}.py"])

    return files


@dataclass
class RunContext:
    """Share run paths, settings, accounting and concurrency controls."""

    root: Path
    credential_root: Path
    run_dir: Path
    settings: Settings
    ledger: Ledger
    prices: Prices
    usage_lock: asyncio.Lock
    identity: RunIdentity | None = None
    model_limiter: asyncio.Semaphore | None = field(init=False)

    def __post_init__(self):
        self.model_limiter = (
            asyncio.Semaphore(self.settings.max_concurrency)
            if self.settings.max_concurrency is not None
            else None
        )


def default_model_factory(name, slot_id, context):
    """Build a configured client with run-wide accounting and limits."""
    return configured_client(
        context.root,
        name,
        context.ledger,
        context.run_dir / "usage.jsonl",
        settings=context.settings,
        credential_root=context.credential_root,
        prices=context.prices,
        usage_lock=context.usage_lock,
        limiter=context.model_limiter,
    )


def merge_slots(left, right):
    """Merge lane updates into the week state, preferring newer values."""
    return {**left, **right}


class WeekState(TypedDict):
    """Describe graph state shared by all content lanes."""

    slots: Annotated[dict, merge_slots]
    warnings: list[str]
    summary: dict
    advisor: str
    advisor_reason: str | None


class LaneState(TypedDict):
    """Describe graph state for one content slot."""

    slot: dict
    attempt: int
    pack: dict | None
    draft: dict | None
    audit: dict | None
    review: dict | None
    image: dict | None
    outcome: str | None
    reasons: list[str]
    feedback: list[str]
    slots: dict


def lane_state(raw):
    """Validate lane fields while ignoring week-level graph updates."""
    return SlotState.model_validate(
        {key: value for key, value in raw.items() if key in SlotState.model_fields}
    )


def failure(error, research=False):
    """Map a stage exception to its existing terminal slot outcome."""
    if isinstance(error, BudgetExhausted):
        return SlotOutcome.failed_budget

    if isinstance(error, ModelFailure):
        if error.outcome in {"failed_budget", "unpriced_model"}:
            return SlotOutcome.failed_budget

        if research and error.outcome in {"failed_output", "failed_research"}:
            return SlotOutcome.failed_research

        if error.outcome in SlotOutcome._value2member_map_:
            return SlotOutcome(error.outcome)

    return SlotOutcome.failed_research if research else SlotOutcome.failed_api


def _route_after_write(state):
    if state.get("outcome"):
        return "collect"

    if state.get("draft"):
        return "validate"

    return "retry"


def _route_after_review(state):
    if state.get("outcome"):
        return "collect"

    if state["review"]["accepted"]:
        return "visual"

    return "retry"


class Pipeline:
    """Coordinate research, content checks, visuals and durable run artifacts."""

    def __init__(
        self,
        context,
        slots,
        model_factory=default_model_factory,
        search_factory=configured_web_search,
        renderer=render_card,
    ):
        self.context = context
        self.root = context.root
        self.plan = slots

        self.rules, _ = load_brand(self.root / "inputs/brand-voice.md")

        self.kb = KnowledgeBase(self.root / "inputs/kb.json")
        self.model_factory = model_factory
        self.search_factory = search_factory
        self.renderer = renderer
        self.searchers = []
        external = context.run_dir / "external-usage.json"

        self.external = (
            ExternalUsage.model_validate_json(external.read_text(encoding="utf-8"))
            if external.exists()
            else ExternalUsage()
        )
        self.external_lock = asyncio.Lock()
        self.search_seen = {}
        self.research_replay_lock = asyncio.Lock()
        self.trails = {
            name: Trail(context.run_dir / (name + ".json"))
            for name in ["research_log", "drafts", "review_report", "visuals", "final", "advisor"]
        }

        for trail in self.trails.values():
            if not trail.path.exists():
                trail.path.write_text("[]", encoding="utf-8")

        self.writers = {}
        self.reviewers = {}
        self.visuals = {}

    def client(self, name, slot_id):
        """Create a model client for an agent and slot."""
        return self.model_factory(name, slot_id, self.context)

    async def record(self, name, record):
        """Append a stage record to its durable trail."""
        await self.trails[name].append(
            record.record_id, record.model_dump(mode="json", exclude={"record_id"})
        )

    def saved_record(self, name, record_id, model, request):
        """Load a saved stage record and verify its request fingerprint."""
        raw = next(
            (
                record
                for record in json.loads(self.trails[name].path.read_text(encoding="utf-8"))
                if record["record_id"] == record_id
            ),
            None,
        )

        if raw is None:
            return None

        record = model.model_validate(raw)

        if record.input_sha256 != hashlib.sha256(request.model_dump_json().encode()).hexdigest():
            raise ValueError("saved stage input mismatch")

        return record

    async def save_search_usage(self, searcher):
        """Persist newly observed search usage under the shared usage lock."""
        mapping = {
            "search_calls": "calls",
            "tavily_calls": "tavily_calls",
            "ddgs_calls": "ddgs_calls",
            "fetch_calls": "fetch_calls",
            "tavily_credits": "tavily_credits",
        }

        async with self.external_lock:
            previous = self.search_seen.setdefault(id(searcher), {})

            for key, attribute in mapping.items():
                value = getattr(searcher, attribute, 0)

                setattr(
                    self.external, key, getattr(self.external, key) + value - previous.get(key, 0)
                )

                previous[key] = value

            self.external.tavily_credits_estimated |= getattr(
                searcher, "tavily_credits_estimated", False
            )

            atomic_json(self.context.run_dir / "external-usage.json", self.external.model_dump())

    async def image_count(self, key):
        """Increment and persist one external image usage counter."""
        async with self.external_lock:
            setattr(self.external, key, getattr(self.external, key) + 1)

            atomic_json(self.context.run_dir / "external-usage.json", self.external.model_dump())

    @staticmethod
    def replay_digest(value):
        """Hash a value with the canonical research replay JSON encoding."""
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()

    def research_request_digest(self, request):
        """Hash a research request using its original planned topic."""
        value = request.model_dump(mode="json")

        if request.slot.focus:
            # Compare focused research against the topic in the original snapshot.
            planned = next(slot for slot in self.plan if slot.slot_id == request.slot.slot_id)

            value["slot"]["topic_id"] = planned.topic_id

        return self.replay_digest(value)

    def _validate_research_replay(self, record_id, request, record):
        receipt_path = self.context.run_dir / "research-replay.json"

        receipts = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        )

        receipt = receipts.get(record_id)
        expected_request = self.research_request_digest(request)
        expected_snapshot = self.replay_digest(self.context.identity.snapshot_files)
        expected_pack = self.replay_digest(record.pack.model_dump(mode="json"))

        if (
            record.action != "resolved"
            or record.failure is not None
            or record.pack.slot_id != request.slot.slot_id
            or record.evidence != record.pack.sources
            or not receipt
            or receipt
            != {
                "request_sha256": expected_request,
                "snapshot_sha256": expected_snapshot,
                "pack_sha256": expected_pack,
            }
        ):
            raise ModelFailure("failed_research")

    async def saved_research(self, record_id, request):
        """Reuse research only when its identity and replay receipt match."""
        path = self.trails["research_log"].path

        raw = next(
            (
                item
                for item in json.loads(path.read_text(encoding="utf-8"))
                if item["record_id"] == record_id
            ),
            None,
        )

        if raw is None:
            return None

        record = ResearchRecord.model_validate(raw)

        if record.slot_id != request.slot.slot_id or record.attempt != request.attempt:
            raise ModelFailure("failed_research")

        if record.pack is None:
            return record

        self._validate_research_replay(record_id, request, record)

        return record

    async def save_research_receipt(self, record_id, request, pack):
        """Persist the request, snapshot and pack fingerprints for replay."""
        receipt = {
            "request_sha256": self.research_request_digest(request),
            "snapshot_sha256": self.replay_digest(self.context.identity.snapshot_files),
            "pack_sha256": self.replay_digest(pack.model_dump(mode="json")),
        }

        async with self.research_replay_lock:
            path = self.context.run_dir / "research-replay.json"
            receipts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

            receipts[record_id] = receipt

            atomic_json(path, receipts)

    def _research_request(self, slot):
        spec = load_type(self.root, slot.type)

        hints = local_file(self.root / "post_types" / slot.type, spec.research.hints).read_text(
            encoding="utf-8"
        )

        request = ResearchRequest(slot=slot, hints=hints)

        return request

    def _restore_research_state(self, state, saved):
        if saved.pack is None:
            state.outcome = failure(ModelFailure(saved.failure or "failed_research"), research=True)
            state.reasons = [state.outcome.value]
        else:
            state.pack = saved.pack
            state.slot.topic_id = saved.pack.topic_id

    async def _record_research_outcome(self, state):
        slot = state.slot
        record_id = record_ids.record_id(slot.slot_id, "research", 1)

        await self.record(
            "research_log",
            ResearchRecord(
                record_id=record_id,
                slot_id=slot.slot_id,
                attempt=1,
                pack=state.pack,
                evidence=state.pack.sources if state.pack else [],
                action="resolved" if state.pack else "failed",
                failure=state.outcome.value if state.outcome else None,
            ),
        )

        return record_id

    async def research_slot(self, state):
        """Resolve research for a slot or record its terminal research failure."""
        slot = state.slot
        agent = None

        try:
            request = self._research_request(slot)
            record_id = record_ids.record_id(slot.slot_id, "research", 1)

            saved = await self.saved_research(record_id, request)

            if saved is not None:
                self._restore_research_state(state, saved)

                return state.model_dump(mode="json")

            searcher = self.search_factory(self.context.credential_root)

            self.searchers.append(searcher)

            agent = ResearchAgent(
                self.root,
                self.client("research", slot.slot_id),
                self.kb,
                searcher,
                self.save_search_usage,
            )

            pack = await agent(request)

            if pack.slot_id != slot.slot_id:
                raise ModelFailure("failed_output")

            state.pack = pack
            state.slot.topic_id = pack.topic_id
        except Exception as error:
            state.outcome = failure(error, research=True)
            state.reasons = [state.outcome.value]

        for record in agent.records if agent else []:
            await self.record("research_log", record)

        record_id = await self._record_research_outcome(state)

        if state.pack is not None:
            await self.save_research_receipt(record_id, request, state.pack)

        return state.model_dump(mode="json")

    async def focused(self, week):
        """Research all explicitly focused slots concurrently."""
        states = [SlotState.model_validate(value) for value in week["slots"].values()]
        focused = [state for state in states if state.slot.focus]

        results = await asyncio.gather(*(self.research_slot(state) for state in focused))

        return {"slots": {state["slot"]["slot_id"]: state for state in results}}

    async def _propose_topics(self, empty, states, prior, warnings):
        proposals = {}
        choices = {}

        for state in empty:
            spec = load_type(self.root, state.slot.type)

            if spec.research.topic_source == "kb":
                choices[state.slot.slot_id] = [entry["id"] for entry in self.kb.entries]
            else:
                choices[state.slot.slot_id] = [angle.id for angle in load_angles(self.root, spec)]

        excluded = [state.slot.topic_id for state in states if state.slot.topic_id]

        for run in prior:
            excluded.extend(run)

        try:
            proposals = (
                await TopicSelector(self.root, self.client("research", "_batch"))(
                    TopicChoiceRequest(choices=choices, exclude=excluded)
                )
            ).assignments
        except Exception:
            warnings.append("topic_selection_failed:code_fallback")

        return proposals

    async def _record_topic_exhaustion(self, state):
        await self.record(
            "research_log",
            ResearchRecord(
                record_id=record_ids.record_id(state.slot.slot_id, "research", 1),
                slot_id=state.slot.slot_id,
                attempt=1,
                evidence=[],
                action="failed",
                failure="topic_pool_exhausted",
            ),
        )

    def _save_resolved_plan(self, states, changed, warnings):
        resolved = []

        for state in states:
            updated = changed.get(state.slot.slot_id) or state.model_dump(mode="json")

            resolved.append(ResolvedSlot.model_validate(updated["slot"]))

        record = ResolvedPlan(
            record_id=record_ids.record_id("_run", "plan", 1),
            slot_id="_run",
            attempt=1,
            slots=resolved,
            warnings=warnings,
        )

        (self.context.run_dir / "plan.resolved.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )

    async def select(self, week):
        """Assign remaining topics and research the resulting slots."""
        states = [SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        empty = [state for state in states if not state.slot.focus]
        warnings = []
        proposals = {}
        prior = history(self.context.run_dir.parent)

        if empty:
            proposals = await self._propose_topics(empty, states, prior, warnings)

        assigned, notes = assign_topics(
            self.root,
            [state.slot for state in states],
            proposals,
            self.kb,
            prior,
            allow_partial=True,
        )

        for state in empty:
            if state.slot.slot_id not in assigned:
                state.outcome = SlotOutcome.failed_research
                state.reasons = ["topic_pool_exhausted"]

        warnings = list(dict.fromkeys(warnings + notes))

        for state in empty:
            if not state.outcome:
                state.slot.topic_id = assigned[state.slot.slot_id]
            else:
                await self._record_topic_exhaustion(state)

        results = await asyncio.gather(
            *(self.research_slot(state) for state in empty if not state.outcome)
        )

        results.extend(state.model_dump(mode="json") for state in empty if state.outcome)

        changed = {state["slot"]["slot_id"]: state for state in results}

        self._save_resolved_plan(states, changed, warnings)

        return {"slots": changed, "warnings": warnings}

    async def _record_writer_failure(self, state, error):
        outcome = failure(error)

        if outcome == SlotOutcome.failed_output:
            state.feedback = ["writer:failed_output"]
        else:
            state.outcome = outcome
            state.reasons = [
                "unpriced_model"
                if isinstance(error, ModelFailure) and error.outcome == "unpriced_model"
                else outcome.value
            ]

        await self.record(
            "drafts",
            DraftRecord(
                record_id=record_ids.record_id(state.slot.slot_id, "writer", state.attempt),
                slot_id=state.slot.slot_id,
                attempt=state.attempt,
                failure=outcome.value,
            ),
        )

    async def write(self, raw):
        """Attempt a draft and record writer failures for retry or collection."""
        state = lane_state(raw)

        state.attempt += 1
        previous_draft = state.draft
        state.draft = None
        state.audit = None
        state.review = None

        request = WriteRequest(
            slot_id=state.slot.slot_id,
            attempt=state.attempt,
            research=state.pack,
            post_type=load_type(self.root, state.slot.type),
            feedback=state.feedback,
            max_tweet_chars=self.context.settings.max_tweet_chars,
            previous_draft=previous_draft,
        )

        try:
            if state.slot.slot_id not in self.writers:
                self.writers[state.slot.slot_id] = Writer(
                    self.root,
                    state.slot.type,
                    self.client(load_type(self.root, state.slot.type).writer, state.slot.slot_id),
                )

            state.draft = await self.writers[state.slot.slot_id](request)
        except Exception as error:
            await self._record_writer_failure(state, error)

        return state.model_dump(mode="json")

    async def validation(self, raw):
        """Normalize a draft and record validator feedback before review."""
        state = lane_state(raw)

        result = validate(
            state.draft,
            state.pack,
            load_type(self.root, state.slot.type),
            self.rules,
            self.context.settings.max_tweet_chars,
        )

        state.audit = DraftAudit(
            original=result.original,
            normalized=[tweet.model_dump() for tweet in result.normalized.tweets],
            fixes=result.fixes,
            violations=result.violations,
            accepted=result.accepted,
        )
        state.feedback = result.violations + length_feedback(
            result, self.context.settings.max_tweet_chars
        )

        await self.record(
            "drafts",
            DraftRecord(
                record_id=record_ids.record_id(state.slot.slot_id, "validator", state.attempt),
                slot_id=state.slot.slot_id,
                attempt=state.attempt,
                audit=state.audit,
            ),
        )

        return state.model_dump(mode="json")

    def _restore_review_state(self, state, saved):
        state.review = saved.review

        if saved.failure:
            state.outcome = SlotOutcome(saved.failure)
            state.reasons = [saved.failure]

        state.feedback = state.review.reasons if state.review else []

    async def review(self, raw):
        """Review an approved draft or reuse its matching saved assessment."""
        state = lane_state(raw)
        spec = load_type(self.root, state.slot.type)

        request = ReviewRequest(
            slot_id=state.slot.slot_id,
            attempt=state.attempt,
            draft=state.audit.approved(),
            research=state.pack,
            post_type=spec,
            rubric=load_rubric(self.root, spec),
        )

        record_id = record_ids.record_id(state.slot.slot_id, "reviewer", state.attempt)

        try:
            saved = self.saved_record("review_report", record_id, ReviewRecord, request)

            if saved:
                self._restore_review_state(state, saved)

                return state.model_dump(mode="json")

            if state.slot.slot_id not in self.reviewers:
                self.reviewers[state.slot.slot_id] = Reviewer(
                    self.root, state.slot.type, self.client("reviewer", state.slot.slot_id)
                )

            state.review = await self.reviewers[state.slot.slot_id](request)

            state.feedback = state.review.reasons
        except Exception as error:
            state.outcome = failure(error)
            state.reasons = [state.outcome.value]

        await self.record(
            "review_report",
            ReviewRecord(
                record_id=record_id,
                slot_id=state.slot.slot_id,
                attempt=state.attempt,
                review=state.review,
                failure=state.outcome.value if state.outcome else None,
                input_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
            ),
        )

        return state.model_dump(mode="json")

    async def retry(self, raw):
        """Drop a slot once its content attempts are exhausted."""
        state = lane_state(raw)

        if state.attempt >= MAX_CONTENT_ATTEMPTS:
            state.outcome = SlotOutcome.dropped_quality
            state.reasons = [
                reason
                for reason in state.feedback
                if not (reason.startswith("tweet_") and ":weighted_length:" in reason)
            ] or ["quality_rejected"]

        return state.model_dump(mode="json")

    def _validate_saved_visual(self, saved, request, state):
        from pathlib import PureWindowsPath

        relative = PureWindowsPath(saved.result.path)

        if relative.anchor or ".." in relative.parts:
            raise ValueError("invalid saved image path")

        path = local_file(self.context.run_dir, Path(*relative.parts).as_posix())

        stored = local_file(
            self.context.run_dir,
            path.with_suffix(".json").relative_to(self.context.run_dir).as_posix(),
        )

        if json.loads(stored.read_text(encoding="utf-8")) != saved.spec.model_dump(
            mode="json"
        ) or overlay_errors(saved.spec, request, self.rules):
            raise ValueError("saved visual artifact mismatch")

        if saved.result.slot_id != state.slot.slot_id:
            raise ValueError("saved visual identity mismatch")

        if saved.result.backend == "pillow_card":
            from PIL import Image

            with Image.open(path) as image:
                if image.format != "PNG" or image.size != CARD_SIZE:
                    raise ValueError("invalid saved card")

                image.verify()

    async def _visual_spec(self, state, request):
        reason = None

        try:
            if state.slot.slot_id not in self.visuals:
                self.visuals[state.slot.slot_id] = VisualAgent(
                    self.root, self.client("visual", state.slot.slot_id)
                )

            spec = await self.visuals[state.slot.slot_id](request)

            if overlay_errors(spec, request, self.rules):
                raise ValueError("invalid_overlay")
        except Exception:
            reason = "visual_fallback"
            spec = fallback_spec(request, self.rules)

        if overlay_errors(spec, request, self.rules):
            raise ValueError("fallback overlay failed grounding")

        return spec, reason

    async def _render_visual(self, state, spec, reason):
        filename = "slot-" + safe_name(state.slot.slot_id)
        spec_path = self.context.run_dir / "images" / (filename + ".json")

        spec_path.parent.mkdir(exist_ok=True)

        spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

        path = spec_path.with_suffix(".png")

        await self.image_count("image_calls")

        try:
            await asyncio.to_thread(self.renderer, spec, path)

            from PIL import Image

            with Image.open(path) as image:
                if image.format != "PNG" or image.size != CARD_SIZE:
                    raise ValueError("invalid rendered card")

                image.verify()

            await self.image_count("pillow_cards")

            backend = "pillow_card"
        except Exception:
            path = spec_path
            backend = "spec_json"
            reason = "pillow_fallback_spec"

        return ImageResult(
            slot_id=state.slot.slot_id,
            path=path.relative_to(self.context.run_dir).as_posix(),
            backend=backend,
            fallback_reason=reason,
        )

    async def visual(self, raw):
        """Reuse or render a grounded visual, retaining fallback artifacts."""
        state = lane_state(raw)

        severity = next(
            (entry["severity"] for entry in self.kb.entries if entry["id"] == state.pack.topic_id),
            None,
        )

        request = VisualRequest(
            slot_id=state.slot.slot_id,
            attempt=state.attempt,
            draft=state.audit.approved(),
            research=state.pack,
            post_type=load_type(self.root, state.slot.type),
            severity=severity,
        )

        record_id = record_ids.record_id(state.slot.slot_id, "visual", state.attempt)
        saved = self.saved_record("visuals", record_id, VisualRecord, request)

        if saved:
            self._validate_saved_visual(saved, request, state)

            state.image = saved.result
            state.outcome = SlotOutcome.shipped

            return state.model_dump(mode="json")

        spec, reason = await self._visual_spec(state, request)

        state.image = await self._render_visual(state, spec, reason)

        state.outcome = SlotOutcome.shipped

        await self.record(
            "visuals",
            VisualRecord(
                record_id=record_id,
                slot_id=state.slot.slot_id,
                attempt=state.attempt,
                spec=spec,
                result=state.image,
                input_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
            ),
        )

        return state.model_dump(mode="json")

    async def collect(self, raw):
        """Record a shipped piece and join the terminal lane into week state."""
        state = lane_state(raw)

        if state.outcome is None:
            raise ValueError("nonterminal slot at join")

        if state.outcome == SlotOutcome.shipped:
            piece = FinalPiece(
                record_id=record_ids.record_id(state.slot.slot_id, "final", state.attempt),
                slot_id=state.slot.slot_id,
                attempt=state.attempt,
                topic_id=state.pack.topic_id,
                topic_name=state.pack.topic_name,
                draft=state.audit.approved(),
                image=state.image,
            )

            await self.record("final", piece)

        return {"slots": {state.slot.slot_id: state.model_dump(mode="json")}}

    async def export(self, week):
        """Export shipped posts in plan order."""
        states = [SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        accepted = []

        for state in states:
            if state.outcome == SlotOutcome.shipped:
                accepted.append(
                    ExportPost(
                        record_id=record_ids.record_id(state.slot.slot_id, "export", state.attempt),
                        slot_id=state.slot.slot_id,
                        attempt=state.attempt,
                        type=state.slot.type,
                        topic_id=state.pack.topic_id,
                        topic_name=state.pack.topic_name,
                        tweets=[tweet.text for tweet in state.audit.approved().tweets],
                        image=state.image.path,
                    ).model_dump(mode="json")
                )

        export_posts(self.context.run_dir, accepted)

        return {}

    async def _record_advice(self, status, reason, output, patch, input_hash):
        (self.context.run_dir / "advice.md").write_text(
            "# Run advice\n\n"
            + (
                "\n".join("- " + line for line in output.recommendations)
                if status == "completed"
                else "Advisor " + status + (": " + reason if reason else "")
            )
            + "\n",
            encoding="utf-8",
        )

        (self.context.run_dir / "advice.patch").write_text(patch, encoding="utf-8")

        await self.record(
            "advisor",
            AdvisorRecord(
                record_id=record_ids.record_id("_run", "advisor", 1),
                slot_id="_run",
                attempt=1,
                status=status,
                output=output if status == "completed" else None,
                reason=reason,
                input_sha256=input_hash,
            ),
        )

    async def advise(self, week):
        """Produce suggest-only advice and record any fail-closed outcome."""
        status = "not_enabled"
        reason = None
        output = None
        patch = ""
        input_hash = None

        if self.context.settings.advisor_enabled:
            try:
                evidence = {
                    name: json.loads(self.trails[name].path.read_text(encoding="utf-8"))
                    for name in ["research_log", "drafts", "review_report", "final"]
                }

                request = AdvisorRequest(
                    evidence=evidence, instructions=instruction_files(self.root, self.plan)
                )

                input_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
                saved = self.saved_record(
                    "advisor", record_ids.record_id("_run", "advisor", 1), AdvisorRecord, request
                )

                if saved:
                    if saved.output is not None:
                        advice_patch(request, saved.output)

                    local_file(self.context.run_dir, "advice.md")
                    local_file(self.context.run_dir, "advice.patch")

                    return {"advisor": saved.status, "advisor_reason": saved.reason}

                output = await Advisor(self.root, self.client("advisor", "_run"))(request)

                patch = advice_patch(request, output)
                status = "completed"
            except Exception as error:
                status = "failed"

                reason = (
                    SlotOutcome.failed_output.value
                    if isinstance(error, InvalidAdvice)
                    else failure(error).value
                )

        await self._record_advice(status, reason, output, patch, input_hash)

        return {"advisor": status, "advisor_reason": reason}

    def _terminal_slots(self, states):
        terminal = {}

        for state in states:
            terminal[state.slot.slot_id] = TerminalSlot(
                outcome=state.outcome,
                attempts=state.attempt,
                topic_id=state.slot.topic_id,
                reasons=state.reasons,
            )

        return terminal

    def _build_summary(self, states, week):
        accepted = [state for state in states if state.outcome == SlotOutcome.shipped]
        totals = self.context.ledger.totals()

        usage = [
            json.loads(line)
            for line in (self.context.run_dir / "usage.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]

        return RunSummary(
            record_id=record_ids.record_id("_run", "summary", 1),
            slot_id="_run",
            attempt=1,
            run_id=self.context.identity.run_id
            if self.context.identity
            else self.context.run_dir.name,
            slots=self._terminal_slots(states),
            shipped=len(accepted),
            confirmed_tokens=totals["tokens"] - totals["uncertain_tokens"],
            uncertain_tokens=totals["uncertain_tokens"],
            confirmed_cost_usd=totals["cost_usd"] - totals["uncertain_cost_usd"],
            uncertain_cost_usd=totals["uncertain_cost_usd"],
            unpriced=any(record["cost_source"] == "unknown" for record in usage)
            or any("unpriced_model" in state.reasons for state in states),
            external_usage=self.external,
            advisor=week["advisor"],
            advisor_reason=week.get("advisor_reason"),
        )

    async def finalize(self, week):
        """Write the run summary and completion log from terminal states."""
        states = [SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        summary = self._build_summary(states, week)

        (self.context.run_dir / "run_summary.json").write_text(
            summary.model_dump_json(indent=2), encoding="utf-8"
        )

        (self.context.run_dir / "run.log").write_text(
            "export: complete\nsummary: complete\n", encoding="utf-8"
        )

        return {"summary": summary.model_dump(mode="json")}

    def _lane_graph(self):
        lane = StateGraph(LaneState)

        for name, node in [
            ("write", self.write),
            ("validate", self.validation),
            ("review", self.review),
            ("retry", self.retry),
            ("visual", self.visual),
            ("collect", self.collect),
        ]:
            lane.add_node(name, node)

        lane.add_conditional_edges(
            START, lambda state: "collect" if state.get("outcome") else "write"
        )
        lane.add_conditional_edges(
            "write",
            lambda state: _route_after_write(state),
        )
        lane.add_conditional_edges(
            "validate", lambda state: "review" if state["audit"]["accepted"] else "retry"
        )
        lane.add_conditional_edges(
            "review",
            lambda state: _route_after_review(state),
        )
        lane.add_conditional_edges(
            "retry", lambda state: "collect" if state.get("outcome") else "write"
        )
        lane.add_edge("visual", "collect")
        lane.add_edge("collect", END)

        return lane

    def graph(self, checkpointer, interrupt_after=None):
        """Compile the content lanes and week workflow with checkpoint support."""
        interrupts = interrupt_after or []
        lane = self._lane_graph()
        graph = StateGraph(WeekState)

        graph.add_node("focused_research", self.focused)
        graph.add_node("topic_selection", self.select)
        graph.add_node(
            "lane",
            lane.compile(
                interrupt_after=[
                    name.split(":", 1)[1] for name in interrupts if name.startswith("lane:")
                ]
            ),
        )
        graph.add_node("export", self.export)
        graph.add_node("advisor", self.advise)
        graph.add_node("summary", self.finalize)
        graph.add_edge(START, "focused_research")
        graph.add_edge("focused_research", "topic_selection")
        graph.add_conditional_edges(
            "topic_selection",
            lambda state: [Send("lane", state["slots"][slot.slot_id]) for slot in self.plan],
        )
        graph.add_edge("lane", "export")
        graph.add_edge("export", "advisor")
        graph.add_edge("advisor", "summary")
        graph.add_edge("summary", END)

        return graph.compile(
            checkpointer=checkpointer,
            interrupt_after=[name for name in interrupts if not name.startswith("lane:")],
        )


class RunInterrupted(RuntimeError):
    """A deliberately interrupted graph has durable unfinished nodes."""


def _prepare_run_inputs(root, slots, output, settings, resume, budget_overrides):
    if resume is not None:
        if slots is not None or output is not None or settings is not None:
            raise ValueError("resume uses saved plan/settings; only budget increases allowed")

        run_dir = resume.resolve()

        snapshot_root, slots, settings, identity = resume_inputs(run_dir, budget_overrides)

        validate_inputs(snapshot_root, slots, settings)
    else:
        if budget_overrides:
            raise ValueError("budget overrides are for resume")

        settings = settings or Settings.load(root)

        validate_inputs(root, slots, settings)

        run_dir = (
            output
            or root
            / "output"
            / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
        ).resolve()

        if run_dir.exists():
            raise ValueError("output run already exists; choose a new directory")

        run_dir.mkdir(parents=True, exist_ok=False)

    return run_dir, slots, settings


def _initialize_new_run(root, run_dir, slots, settings):
    snapshot_root = snapshot(root, run_dir, slots, settings)

    identity = RunIdentity(
        thread_id=uuid4().hex,
        run_id=run_dir.name,
        snapshot_files=snapshot_hashes(snapshot_root),
    )

    atomic_json(run_dir / "run_identity.json", identity.model_dump())
    atomic_json(run_dir / "runtime-settings.json", settings.model_dump())
    atomic_json(run_dir / "external-usage.json", ExternalUsage().model_dump())

    (run_dir / "usage.jsonl").write_text("", encoding="utf-8")

    return snapshot_root, identity


async def _completed_checkpoint(graph, config, slots):
    saved = await graph.aget_state(config)

    state = WeekSnapshot.model_validate(saved.values)

    if list(state.slots) != [slot.slot_id for slot in slots]:
        raise ValueError("checkpoint plan mismatch")

    if not saved.next and state.summary is not None:
        return state.summary

    if not saved.next:
        raise ValueError("checkpoint has no unfinished nodes")

    return None


async def _invoke_pipeline(context, graph, config, slots, resume):
    run_dir = context.run_dir

    try:
        result = await graph.ainvoke(
            None
            if resume is not None
            else {
                "slots": {
                    slot.slot_id: SlotState(slot=slot).model_dump(mode="json") for slot in slots
                },
                "warnings": [],
            },
            config,
        )

        if "summary" not in result:
            raise RunInterrupted("graph interrupted; resume saved run")

        return RunSummary.model_validate(result["summary"])
    finally:
        saved = await graph.aget_state(config)

        atomic_json(
            run_dir / "state.json",
            WeekSnapshot.model_validate(saved.values).model_dump(mode="json"),
        )

        context.prices.close()


async def _execute_pipeline(
    context, pipeline, slots, saver, resume, model_factory, interrupt_after
):
    run_dir = context.run_dir
    settings = context.settings
    identity = context.identity
    graph = pipeline.graph(saver, interrupt_after)

    config = {
        "configurable": {"thread_id": identity.thread_id},
        "recursion_limit": GRAPH_RECURSION_LIMIT,
    }

    if resume is not None:
        completed = await _completed_checkpoint(graph, config, slots)

        if completed is not None:
            return completed

    context.ledger = Ledger(
        run_dir / "ledger.json", settings.run_token_budget, settings.run_cost_budget_usd
    )

    atomic_json(run_dir / "runtime-settings.json", settings.model_dump())

    context.prices = Prices(update=model_factory is None)

    return await _invoke_pipeline(context, graph, config, slots, resume)


async def run_pipeline(
    root: Path,
    slots: list[ResolvedSlot] | None = None,
    output: Path | None = None,
    settings: Settings | None = None,
    model_factory=None,
    search_factory=None,
    renderer=None,
    *,
    resume: Path | None = None,
    budget_overrides=None,
    interrupt_after=None,
) -> RunSummary:
    """Run or resume a content plan with durable checkpoints and accounting."""
    root = root.resolve()

    run_dir, slots, settings = _prepare_run_inputs(
        root, slots, output, settings, resume, budget_overrides
    )

    with RunLock(run_dir, create=resume is None):
        if resume is None:
            snapshot_root, identity = _initialize_new_run(root, run_dir, slots, settings)
        else:
            snapshot_root, slots, settings, identity = resume_inputs(run_dir, budget_overrides)

        context = RunContext(
            snapshot_root, root, run_dir, settings, None, None, asyncio.Lock(), identity
        )

        pipeline = Pipeline(
            context,
            slots,
            model_factory or default_model_factory,
            search_factory or configured_web_search,
            renderer or render_card,
        )

        async with AsyncSqliteSaver.from_conn_string(str(run_dir / "checkpoint.sqlite")) as saver:
            return await _execute_pipeline(
                context, pipeline, slots, saver, resume, model_factory, interrupt_after
            )
