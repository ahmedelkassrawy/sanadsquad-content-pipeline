from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotOutcome(str, Enum):
    shipped = "shipped"
    dropped_quality = "dropped_quality"
    failed_research = "failed_research"
    failed_api = "failed_api"
    failed_budget = "failed_budget"
    failed_output = "failed_output"


class CallFailureKind(str, Enum):
    rate_limit = "rate_limit"
    auth = "auth"
    payment = "payment"
    timeout = "timeout"
    connection = "connection"
    server = "server"
    provider_error = "provider_error"
    truncated = "truncated"
    malformed_output = "malformed_output"
    malformed_envelope = "malformed_envelope"
    output_rejected = "output_rejected"
    unknown = "unknown"


class CallFinishReason(str, Enum):
    stop = "stop"
    end_turn = "end_turn"
    stop_sequence = "stop_sequence"
    tool_calls = "tool_calls"
    function_call = "function_call"
    length = "length"
    max_tokens = "max_tokens"
    error = "error"
    content_filter = "content_filter"
    unknown = "unknown"


class ResolvedSlot(Contract):
    slot_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    focus: str | None = None
    topic_id: str | None = None


class SourceRecord(Contract):
    source_id: str = Field(pattern=r"^(KB-\d+|STAT-\d+|WEB-[a-f0-9]+)$")
    kind: Literal["kb", "stat", "web"]
    url: str | None = None
    title: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence_text: str
    status: Literal["ok", "snippet_only", "fetch_failed"] = "ok"

    @model_validator(mode="after")
    def source_kind(self):
        if not self.source_id.startswith({"kb": "KB-", "stat": "STAT-", "web": "WEB-"}[self.kind]):
            raise ValueError("source id must match kind")
        if self.status == "ok" and not self.evidence_text.strip():
            raise ValueError("citable evidence cannot be empty")
        return self


class ResearchPack(Contract):
    slot_id: str
    topic_id: str
    topic_name: str
    sources: list[SourceRecord]

    @model_validator(mode="after")
    def unique_sources(self):
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source ids")
        if not any(s.status == "ok" for s in self.sources):
            raise ValueError("failed_research: no citable evidence")
        return self


class Tweet(Contract):
    text: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


class Draft(Contract):
    slot_id: str
    attempt: int = Field(default=1, ge=1)
    tweets: list[Tweet] = Field(min_length=1)


class ValidationResult(Contract):
    original: Draft
    normalized: Draft
    fixes: list[str]
    violations: list[str]
    accepted: bool


class Review(Contract):
    slot_id: str
    scores: dict[str, float]
    reasons: list[str]
    hard_fail: list[str]
    accepted: bool

    @model_validator(mode="after")
    def score_range(self):
        if not self.scores or any(not 1 <= s <= 5 for s in self.scores.values()):
            raise ValueError("review scores must be 1..5")
        if self.hard_fail and self.accepted:
            raise ValueError("hard failure cannot approve")
        return self


class ImageSpec(Contract):
    concept: str
    scene: str
    palette: list[str]
    overlay_text: str
    alt_text: str
    severity: str | None = None


class ImageResult(Contract):
    slot_id: str
    path: str
    backend: str
    fallback_reason: str | None = None


class UsageRecord(Contract):
    call_id: str
    slot_id: str | None = None
    node: str | None = None
    attempt: int | None = None
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated: bool = False
    cost_usd: float | None = Field(default=None, ge=0)
    cost_source: str
    status: Literal["confirmed", "uncertain"] = "confirmed"
    overshoot_tokens: int = Field(default=0, ge=0)
    failure_kind: CallFailureKind | None = None
    http_status: int | None = Field(default=None, strict=True, ge=100, le=599)
    finish_reason: CallFinishReason | None = None


class Units(Contract):
    min: int = Field(ge=1)
    max: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.max < self.min:
            raise ValueError("units max below min")
        return self


class ResearchConfig(Contract):
    hints: str
    topic_source: Literal["kb", "angles"]
    angles: str | None = None

    @model_validator(mode="after")
    def angle_file(self):
        if self.topic_source == "angles" and not self.angles:
            raise ValueError("angles source needs angles file")
        return self


class ReviewConfig(Contract):
    pass_avg: float = Field(ge=1, le=5)
    hard_fail: list[str]


class VisualConfig(Contract):
    template: str
    severity_badge: Literal["auto", "never"]


class PostType(Contract):
    name: str
    writer: str
    rubric: str
    units: Units
    numbering: bool
    research: ResearchConfig
    review: ReviewConfig
    visual: VisualConfig


class BrandRules(Contract):
    brand_name: str
    banned_words: list[str]
    banned_characters: list[str]
    allowed_links: list[str]
    safe_fixes: list[Literal["banned_characters", "whitespace", "curly_quotes", "trailing_hashtags"]]
    approved_stats: dict[str, str]
    palette: list[str]


class AgentConfig(Contract):
    model: str | None = None
    max_output_tokens: int = Field(ge=1)
    reasoning_effort: Literal["none", "low", "medium", "high"]
    skills: list[str]
