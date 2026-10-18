"""Rubric-driven review scoring: the reviewer wire schema and one assessment judgement."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from .contracts import ReviewAssessment, ReviewRequest
from .model_client import ModelFailure
from .models import Review


@dataclass(frozen=True)
class ReviewJudgement:
    """One assessment judged: accepted, rejected with reasons, or malformed."""

    verdict: Literal["accepted", "rejected", "malformed"]
    review: Review | None = None
    errors: list[dict] = field(default_factory=list)


class _ReasonFields(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, str] = Field(init=False)


def review_wire_schema(request: ReviewRequest):
    """Build the request-specific structured output schema for one review request."""
    criteria = list(request.rubric.criteria)
    flags = list(dict.fromkeys(request.post_type.review.hard_fail))

    # Aliases keep file-configured names intact, including nonidentifiers and
    # names that would otherwise collide with Pydantic methods/private fields.
    scores = create_model(
        "ReviewScores",
        __config__=ConfigDict(extra="forbid"),
        **{f"field_{i}": (float, Field(alias=key, ge=1, le=5)) for i, key in enumerate(criteria)},
    )

    hard_fail = create_model(
        "ReviewHardFail",
        __config__=ConfigDict(extra="forbid"),
        **{f"field_{i}": (bool, Field(alias=key)) for i, key in enumerate(flags)},
    )

    reason_fields = {f"field_{i}": (str, Field(alias=key)) for i, key in enumerate(criteria)}

    for key in flags:
        if key not in criteria:
            reason_fields[f"field_{len(reason_fields)}"] = (str, Field(default="", alias=key))

    reasons = create_model("ReviewReasons", __base__=_ReasonFields, **reason_fields)

    return create_model(
        "ReviewAssessment",
        __base__=ReviewAssessment,
        scores=(scores, ...),
        reasons=(reasons, ...),
        hard_fail=(hard_fail, ...),
    )


def canonical_review(assessment) -> ReviewAssessment:
    """Fold a wire assessment into the canonical review contract."""
    # Omitted false-flag reasons are allowed. Explicit nulls remain invalid in
    # the canonical string dictionary rather than disappearing during conversion.
    return ReviewAssessment.model_validate(assessment.model_dump(by_alias=True, exclude_unset=True))


def review_contract_errors(request: ReviewRequest, assessment: ReviewAssessment) -> list[dict]:
    """List every way the assessment breaks the rubric contract as validation error data."""
    errors = []
    criteria = set(request.rubric.criteria)
    hard = set(request.post_type.review.hard_fail)

    for name, required in [("scores", criteria), ("hard_fail", hard)]:
        values = getattr(assessment, name)

        errors.extend(
            {"type": "missing", "loc": (name, key), "input": None}
            for key in sorted(required - set(values))
        )

        if set(values) - required:
            errors.append(
                {
                    "type": "value_error",
                    "loc": (name,),
                    "input": None,
                    "ctx": {"error": ValueError("unexpected rubric fields")},
                }
            )

    for name in ["slot_id", "attempt"]:
        if getattr(assessment, name) != getattr(request, name):
            errors.append(
                {
                    "type": "value_error",
                    "loc": (name,),
                    "input": None,
                    "ctx": {"error": ValueError("identity mismatch")},
                }
            )

    for key in criteria:
        if key in assessment.scores and not 1 <= assessment.scores[key] <= 5:
            errors.append(
                {
                    "type": "value_error",
                    "loc": ("scores", key),
                    "input": None,
                    "ctx": {"error": ValueError("score outside 1..5")},
                }
            )

    for key in criteria | {key for key in hard if assessment.hard_fail.get(key)}:
        if not assessment.reasons.get(key, "").strip():
            errors.append({"type": "missing", "loc": ("reasons", key), "input": None})

    return errors


def judge_review(request: ReviewRequest, assessment: ReviewAssessment) -> ReviewJudgement:
    """Judge one assessment: accepted, rejected with reasons, or malformed."""
    errors = review_contract_errors(request, assessment)

    if errors:
        return ReviewJudgement(verdict="malformed", errors=errors)

    review = _scored_review(request, assessment)

    return ReviewJudgement(verdict="accepted" if review.accepted else "rejected", review=review)


def score_review(request: ReviewRequest, assessment: ReviewAssessment) -> Review:
    """Return the scored review, failing closed when the assessment is malformed."""
    judgement = judge_review(request, assessment)

    if judgement.review is None:
        raise ModelFailure("failed_output")

    return judgement.review


def _scored_review(request: ReviewRequest, assessment: ReviewAssessment) -> Review:
    """Turn a contract-complete assessment into the review the pipeline records."""
    criteria = set(request.rubric.criteria)
    hard_fail = [key for key, value in assessment.hard_fail.items() if value]
    config = request.post_type.review
    average = sum(assessment.scores.values()) / len(criteria)
    below_floor = any(score < config.min_score for score in assessment.scores.values())
    accepted = average >= config.pass_avg and not below_floor and not hard_fail

    reasons = [
        assessment.reasons[key]
        for key in request.rubric.criteria
        if assessment.scores[key] < config.pass_avg
    ]

    reasons.extend(assessment.reasons[key] for key in hard_fail)

    # A criterion and a hard-fail flag often carry the same sentence.
    reasons = list(dict.fromkeys(reasons))

    if not accepted and not reasons:
        reasons = [assessment.reasons[key] for key in request.rubric.criteria]

    return Review(
        slot_id=request.slot_id,
        scores=assessment.scores,
        reasons=reasons,
        hard_fail=hard_fail,
        accepted=accepted,
    )
