"""Private request-specific tool representation of the canonical review contract."""

from pydantic import BaseModel, ConfigDict, Field, create_model
from .contracts import ReviewAssessment


class _ReasonFields(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, str] = Field(init=False)


def review_wire_schema(request):
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


def canonical_review(assessment):
    # Omitted false-flag reasons are allowed. Explicit nulls remain invalid in
    # the canonical string dictionary rather than disappearing during conversion.
    return ReviewAssessment.model_validate(assessment.model_dump(by_alias=True, exclude_unset=True))
