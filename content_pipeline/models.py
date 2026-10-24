from datetime import datetime, timezone
from enum import Enum
import re
from typing import Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotOutcome(str, Enum):
    shipped = "shipped"
    dropped_quality = "dropped_quality"
    dropped_weekly_mix = "dropped_weekly_mix"
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
    # For an angle that picks one of several sources: the source code chose for this week.
    angle_source: str | None = None
    # Marketing-only planning data. These fields are persisted with the resolved plan so the
    # writer, validator and run log agree on the week's assigned shape.
    format: str | None = None
    service_line: str | None = None
    # Set only for a cross-service post: [primary, secondary]. service_line stays the primary.
    service_lines: list[str] | None = None
    cta_allowed: bool = True
    # Set from a plan's optional top-level `week` and copied into snapshots for operators.
    marketing_week: str | None = Field(default=None, pattern=r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")

    @model_serializer(mode="wrap")
    def _omit_unset_service_lines(self, handler: SerializerFunctionWrapHandler):
        # Single-service slots serialize exactly as before the field existed.
        data = handler(self)
        if data.get("service_lines") is None:
            data.pop("service_lines", None)
        return data


def slot_service_lines(slot) -> list[str]:
    """Every service line a marketing slot counts toward: both for a cross-service post."""
    if slot.service_lines:
        return list(slot.service_lines)
    return [slot.service_line] if slot.service_line else []


class SourceRecord(Contract):
    # KB-* remains readable solely for run folders saved before the SS migration.
    source_id: str = Field(pattern=r"^(?:(?:SS|KB)-\d+|STAT-\d+|WEB-[a-f0-9]+)$")
    kind: Literal["kb", "stat", "web"]
    url: str | None = None
    title: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Search backends may supply this. A retrieval time is not a publication date.
    published_at: datetime | None = None
    evidence_text: str
    status: Literal["ok", "snippet_only", "fetch_failed"] = "ok"
    # Service lines whose allowlist holds this web source's domain (cross-service posts only).
    service_lines: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_empty_service_lines(self, handler: SerializerFunctionWrapHandler):
        # Keeps saved packs and replay hashes byte-identical for single-service sources.
        data = handler(self)
        if not data.get("service_lines"):
            data.pop("service_lines", None)
        return data

    @model_validator(mode="after")
    def source_kind(self):
        expected = {"kb": ("SS-", "KB-"), "stat": ("STAT-",), "web": ("WEB-",)}[self.kind]
        if not self.source_id.startswith(expected):
            raise ValueError("source id must match kind")

        if self.status == "ok" and not self.evidence_text.strip():
            raise ValueError("citable evidence cannot be empty")

        return self


class ResearchPack(Contract):
    slot_id: str
    topic_id: str
    topic_name: str
    sources: list[SourceRecord]
    kb_allowed: bool = False

    @model_validator(mode="after")
    def unique_sources(self):
        ids = [s.source_id for s in self.sources]

        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source ids")

        if not any(s.status == "ok" for s in self.sources):
            raise ValueError("failed_research: no citable evidence")

        return self

    @property
    def fetched_urls(self) -> set[str]:
        """URLs fetched successfully for this run and therefore safe to publish."""
        return {
            source.url
            for source in self.sources
            if source.kind == "web" and source.status == "ok" and source.url
        }


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
    fallback_detail: list[str] | None = None


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


class MarketingFormat(Contract):
    """One data-loaded shape a marketing slot may use."""

    id: str = Field(min_length=1)
    units: Units
    link_rule: Literal["required", "forbidden", "optional"]
    length_hint: str = Field(min_length=1)
    numbering: bool = False


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
    # Any single criterion below this floor rejects the piece, whatever the average.
    min_score: float = Field(default=1, ge=1, le=5)
    hard_fail: list[str]

    @model_validator(mode="after")
    def floor_not_above_pass(self):
        if self.min_score > self.pass_avg:
            raise ValueError("min_score cannot exceed pass_avg")

        return self


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
    # Words that make a quality or outcome claim; allowed only when the slot's evidence uses them.
    claim_words: list[str] = Field(default_factory=list)
    # Optional whole-post limits. None leaves the rule disabled for this post type.
    max_approved_stats: int | None = Field(default=None, ge=1)
    max_brand_mentions: int | None = Field(default=None, ge=1)
    # Hook and closing styles handed out to the slots of this type in plan order (the first slot
    # gets the first style, and so on, wrapping around), so a week's threads do not all open and
    # end the same way. Empty means the writer is free.
    hook_styles: list[str] = Field(default_factory=list)
    closing_styles: list[str] = Field(default_factory=list)
    # Marketing uses this table to select the effective unit, link and numbering rules.
    formats: list[MarketingFormat] = Field(default_factory=list)
    # Deliberately separate from `formats`: content can add a format without changing rotation.
    # An absent value is the compatibility signal for pre-rotation snapshots.
    format_order: list[str] = Field(default_factory=list)
    weekly_topic_similarity: float = Field(default=0.6, ge=0, le=1)

    @model_validator(mode="after")
    def styles_not_blank(self):
        if any(not style.strip() for style in self.hook_styles + self.closing_styles):
            raise ValueError("hook and closing styles cannot be blank")

        if len({format.id for format in self.formats}) != len(self.formats):
            raise ValueError("duplicate marketing format")

        unknown_format_order = set(self.format_order) - {format.id for format in self.formats}
        if unknown_format_order:
            raise ValueError("unknown marketing format in format_order")

        if len(self.format_order) != len(set(self.format_order)):
            raise ValueError("duplicate marketing format in format_order")

        if self.formats and any(
            format.units.min < self.units.min or format.units.max > self.units.max
            for format in self.formats
        ):
            raise ValueError("format units outside post type units")

        return self


class BrandRules(Contract):
    brand_name: str
    banned_words: list[str]
    banned_characters: list[str]
    allowed_links: list[str]
    safe_fixes: list[
        Literal[
            "banned_characters",
            "whitespace",
            "curly_quotes",
            "trailing_hashtags",
            "markdown",
        ]
    ]
    approved_stats: dict[str, str]
    palette: list[str]


SOURCE_GROUPS = ("blogs", "docs", "production", "analysis", "figures")
SERVICE_LINES = ("web3-security", "pentesting", "agentic-security", "agentic-payments")


class SourceGroups(Contract):
    blogs: list[str]
    docs: list[str]
    production: list[str]
    analysis: list[str]
    figures: list[str]

    @model_validator(mode="after")
    def domains_are_nonempty_bare_hosts(self):
        for group in SOURCE_GROUPS:
            domains = getattr(self, group)
            if not domains:
                raise ValueError(f"source group {group} must not be empty")
            if any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain, re.I)
                for domain in domains
            ):
                raise ValueError("source domains must be bare hostnames")
        return self


class SourceRegistry(Contract):
    """Allowlisted web-source domains, loaded once before a run starts."""

    web3_security: SourceGroups = Field(alias="web3-security")
    pentesting: SourceGroups
    agentic_security: SourceGroups = Field(alias="agentic-security")
    agentic_payments: SourceGroups = Field(alias="agentic-payments")
    deny: list[str] = Field(alias="_deny")

    def domains_for(self, service_line: str) -> set[str]:
        if service_line not in SERVICE_LINES:
            raise ValueError("unknown service: " + service_line)
        groups = getattr(self, service_line.replace("-", "_"))
        return {domain for group in SOURCE_GROUPS for domain in getattr(groups, group)}

    def domains_by_service(self, service_lines) -> dict[str, set[str]]:
        """Allowlisted domains for each of several service lines, keyed by line."""
        return {line: self.domains_for(line) for line in service_lines}


class AgentConfig(Contract):
    model: str | None = None
    max_output_tokens: int = Field(ge=1)
    reasoning_effort: Literal["none", "low", "medium", "high"]
    skills: list[str]
