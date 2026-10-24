from typing import Literal
from pydantic import Field, StrictStr, model_validator
from .models import (
    Contract,
    Draft,
    ImageResult,
    ImageSpec,
    MarketingFormat,
    PostType,
    ResearchPack,
    ResolvedSlot,
    Review,
    SlotOutcome,
    SourceRecord,
)


class Angle(Contract):
    id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    name: StrictStr
    source_ids: list[StrictStr] | None = None
    # With pick_one, source_ids are candidates and code uses only the first one no other slot of
    # the week covers, so a marketing post never repeats a thread's topic.
    pick_one: bool = False
    # The approved stats this angle may use; None means all of them, [] means none. Different stats per angle
    # keep two marketing posts in one week from saying the same thing.
    stat_ids: list[StrictStr] | None = None
    service_line: (
        Literal["web3-security", "pentesting", "agentic-security", "agentic-payments"] | None
    ) = None
    # Terms used only to resolve the service line of an explicitly focused marketing slot.
    focus_terms: list[StrictStr] = Field(default_factory=list)
    # The short, data-loaded first web query for an open marketing angle.
    seed_query: StrictStr | None = None
    kb_allowed: bool = False
    subject: StrictStr | None = None
    # A format that best serves this angle, used if it does not break the weekly limits.
    preferred_format: StrictStr | None = None

    @model_validator(mode="after")
    def nonempty_name(self):
        if not self.name.strip():
            raise ValueError("angle needs a nonempty name")

        if self.source_ids is not None and (
            not self.source_ids or len(self.source_ids) != len(set(self.source_ids))
        ):
            raise ValueError("configured angle evidence needs unique source ids")

        if self.pick_one and not self.source_ids:
            raise ValueError("pick_one needs candidate source ids")

        if self.kb_allowed and self.subject != "vulnerability-class":
            raise ValueError("kb_allowed angles must be vulnerability-class")

        if self.seed_query is not None and not self.seed_query.strip():
            raise ValueError("angle seed query must be nonempty")

        # An empty list is allowed: the angle uses no firm stats at all.
        if self.stat_ids is not None and len(self.stat_ids) != len(set(self.stat_ids)):
            raise ValueError("angle stat ids must be unique")

        return self


class AnglesFile(Contract):
    angles: list[Angle] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({angle.id for angle in self.angles}) != len(self.angles):
            raise ValueError("duplicate angle ids")

        return self


class ResearchRequest(Contract):
    slot: ResolvedSlot
    attempt: int = 1
    hints: str


class ResearchAction(Contract):
    slot_id: str
    attempt: int
    action: Literal["search_kb", "web_search", "finish"]
    query: str = ""
    source_ids: list[str] = Field(default_factory=list)
    topic_id: str | None = None


class ResearchTurn(Contract):
    request: ResearchRequest
    evidence: list[SourceRecord]
    searches_remaining: int
    # Defaults keep research turns serialized by earlier runs loadable.
    available_actions: list[Literal["search_kb", "web_search", "finish"]] = Field(
        default_factory=list
    )


class TopicInfo(Contract):
    """What the topic chooser is told about one candidate id, so it does not pick blind."""

    title: str
    summary: str = ""
    category: str | None = None
    severity: str | None = None


class TopicChoiceRequest(Contract):
    """One batch of open slots. Each slot's choices already hold only ids that code will accept."""

    slot_id: str = "_batch"
    attempt: int = 1
    choices: dict[str, list[str]]
    # Every id in `choices`, described.
    catalog: dict[str, TopicInfo] = Field(default_factory=dict)
    # This week's focused slots that already have a topic: slot id -> "SS-01 Reentrancy".
    taken: dict[str, str] = Field(default_factory=dict)


class TopicChoices(Contract):
    slot_id: str
    attempt: int
    assignments: dict[str, str]
    # slot id -> one line on why that topic. Optional and informational: code never acts on it.
    reasons: dict[str, str] = Field(default_factory=dict)


class WriteStyle(Contract):
    """The hook and closing style code assigned to one slot; None leaves that part to the writer."""

    hook: str | None = None
    closing: str | None = None


class WriteRequest(Contract):
    slot_id: str
    attempt: int = Field(ge=1, le=2)
    research: ResearchPack
    post_type: PostType
    feedback: list[str]
    max_tweet_chars: int = Field(default=280, ge=1, le=280)
    previous_draft: Draft | None = None
    # The slot's assigned hook and closing, so threads of one week are written differently.
    style: WriteStyle | None = None
    # The data-loaded marketing shape selected during planning, if this is a marketing slot.
    format: MarketingFormat | None = None
    cta_allowed: bool = True


class Rubric(Contract):
    criteria: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self):
        if len(self.criteria) != len(set(self.criteria)):
            raise ValueError("duplicate rubric criteria")

        return self


class ReviewRequest(Contract):
    slot_id: str
    attempt: int
    draft: Draft
    research: ResearchPack
    post_type: PostType
    rubric: Rubric


class ReviewAssessment(Contract):
    slot_id: str
    attempt: int
    scores: dict[str, float]
    reasons: dict[str, str]
    hard_fail: dict[str, bool]


class VisualRequest(Contract):
    slot_id: str
    attempt: int
    draft: Draft
    research: ResearchPack
    post_type: PostType
    severity: str | None = None


class VisualOutput(Contract):
    slot_id: str
    attempt: int
    spec: ImageSpec


class AuditTweet(Contract):
    # Rejected normalized candidates can be empty; accepted Draft stays strict.
    text: str
    source_ids: list[str]


class DraftAudit(Contract):
    original: Draft
    normalized: list[AuditTweet]
    fixes: list[str]
    violations: list[str]
    accepted: bool
    sentence_length_stats: dict[str, float | int] | None = None

    def approved(self):
        if not self.accepted:
            raise ValueError("draft has not passed validation")

        return Draft(
            slot_id=self.original.slot_id,
            attempt=self.original.attempt,
            tweets=[tweet.model_dump() for tweet in self.normalized],
        )


class SlotState(Contract):
    slot: ResolvedSlot
    attempt: int = Field(default=0, ge=0, le=2)
    pack: ResearchPack | None = None
    draft: Draft | None = None
    audit: DraftAudit | None = None
    review: Review | None = None
    image: ImageResult | None = None
    outcome: SlotOutcome | None = None
    reasons: list[str] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    # The exception behind a failed_* outcome, with anything key-like removed.
    error: str | None = None


class Trace(Contract):
    record_id: str
    slot_id: str
    attempt: int


class ResearchRecord(Trace):
    pack: ResearchPack | None = None
    evidence: list[SourceRecord]
    action: str
    query: str = ""
    query_attempts: list[dict] = Field(default_factory=list)
    url_events: list[dict] = Field(default_factory=list)
    failure: str | None = None


class DraftRecord(Trace):
    input_sha256: str | None = None
    audit: DraftAudit | None = None
    failure: str | None = None
    failure_detail: str | None = None


class ReviewRecord(Trace):
    input_sha256: str | None = None
    review: Review | None = None
    failure: str | None = None


class VisualRecord(Trace):
    input_sha256: str | None = None
    spec: ImageSpec
    result: ImageResult


class FinalPiece(Trace):
    topic_id: str
    topic_name: str
    draft: Draft
    image: ImageResult


class ExportPost(Trace):
    type: str
    topic_id: str
    topic_name: str
    tweets: list[str]
    image: str


class ResolvedPlan(Trace):
    slots: list[ResolvedSlot]
    warnings: list[str]
    # slot id -> the model's one-line reason, only for picks that code accepted as proposed.
    topic_reasons: dict[str, str] = Field(default_factory=dict)
    marketing_week: str | None = None


class ExternalUsage(Contract):
    search_calls: int = 0
    tavily_calls: int = 0
    ddgs_calls: int = 0
    fetch_calls: int = 0
    tavily_credits: float = 0
    tavily_credits_estimated: bool = False
    image_calls: int = 0
    cloudflare_images: int = 0
    cloudflare_neurons: int = 0
    pillow_cards: int = 0


class TerminalSlot(Contract):
    outcome: SlotOutcome
    attempts: int
    topic_id: str | None
    reasons: list[str]
    # Why a model, search or budget call failed, so a failed_api slot is diagnosable from the
    # summary alone. Key-like strings are redacted.
    error: str | None = None


class AgentUsage(Contract):
    """Model calls of one agent, summed from usage.jsonl. The uncertain part is included."""

    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: float
    uncertain_tokens: int = Field(ge=0)
    uncertain_cost_usd: float


class RunSummary(Trace):
    run_id: str
    slots: dict[str, TerminalSlot]
    shipped: int
    confirmed_tokens: int
    uncertain_tokens: int
    confirmed_cost_usd: float
    uncertain_cost_usd: float
    unpriced: bool
    external_usage: ExternalUsage
    cost_scope: str = "LLM USD only; search/image calls counted but not priced. Pre-call token estimates can overshoot; uncertain dispatched charges retained."
    advisor: Literal["not_enabled", "completed", "failed"] = "not_enabled"
    advisor_reason: str | None = None
    # Per-agent totals (research, writer, reviewer, visual, advisor), summed from usage.jsonl. They
    # can differ from the run totals (ledger) after a crash/resume or an unpriced response.
    # Empty in run folders written before this field existed.
    agents: dict[str, AgentUsage] = Field(default_factory=dict)


class Estimate(Contract):
    slots: int
    worst_case_tokens: int
    worst_case_cost_usd: float | None
    token_budget: int
    cost_budget_usd: float
    warnings: list[str]


class LedgerEntry(Contract):
    tokens: int = Field(ge=0)
    cost_usd: float | None
    status: Literal["reserved", "dispatched", "released", "confirmed", "uncertain"]


class WeekSnapshot(Contract):
    slots: dict[str, SlotState]
    warnings: list[str]
    summary: RunSummary | None = None
    advisor: str | None = None
    advisor_reason: str | None = None


class InstructionFile(Contract):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str


class AdvisorRequest(Contract):
    slot_id: str = "_run"
    attempt: int = 1
    evidence: dict[str, list[dict]]
    instructions: dict[str, InstructionFile]


class InstructionEdit(Contract):
    path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    before: str = Field(min_length=1, max_length=2000)
    after: str = Field(min_length=1, max_length=2000)


class AdvisorOutput(Contract):
    slot_id: str
    attempt: int
    recommendations: list[str] = Field(max_length=10)
    edits: list[InstructionEdit] = Field(max_length=8)

    @model_validator(mode="after")
    def bounded_recommendations(self):
        if any(not line.strip() or len(line) > 600 for line in self.recommendations):
            raise ValueError("advice recommendations must be concise")

        return self


class AdvisorRecord(Trace):
    input_sha256: str | None = None
    status: Literal["not_enabled", "completed", "failed"]
    output: AdvisorOutput | None = None
    reason: str | None = None


class RunIdentity(Contract):
    version: Literal[1] = 1
    thread_id: str
    run_id: str
    snapshot_files: dict[str, str]
    # None is a run saved before weekly marketing rotation; it retains offset zero on resume.
    marketing_week: str | None = Field(default=None, pattern=r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
