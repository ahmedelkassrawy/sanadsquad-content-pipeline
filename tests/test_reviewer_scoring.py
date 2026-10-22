"""Public judgement of the review scoring module: accept, reject with reasons or malformed."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from content_pipeline.agents import load_rubric
from content_pipeline.config import load_type
from content_pipeline.contracts import ReviewAssessment, ReviewRequest
from content_pipeline.model_client import ModelFailure
from content_pipeline.models import Draft, ResearchPack, ReviewConfig, Tweet
from content_pipeline.research import KnowledgeBase
from content_pipeline.review_scoring import (
    canonical_review,
    judge_review,
    review_wire_schema,
    score_review,
)

ROOT = Path(__file__).resolve().parents[1]


def review_request():
    """A marketing-post review request for one evidence-backed tweet."""
    spec = load_type(ROOT, "marketing-post")
    source = KnowledgeBase(ROOT / "inputs/kb.json").source("SS-01")

    return ReviewRequest(
        slot_id="a",
        attempt=1,
        draft=Draft(
            slot_id="a",
            tweets=[
                Tweet(
                    text="External calls can reenter before state is finalized.",
                    source_ids=["SS-01"],
                )
            ],
        ),
        research=ResearchPack(
            slot_id="a", topic_id="SS-01", topic_name=source.title, sources=[source]
        ),
        post_type=spec,
        rubric=load_rubric(ROOT, spec),
    )


def passing_scores(request, value=4.0):
    """Every rubric criterion at one score, four by default."""

    return {key: value for key in request.rubric.criteria}


def criterion_reasons(request, value="Supported by the supplied evidence."):
    """One passing reason per rubric criterion."""

    return {key: value for key in request.rubric.criteria}


def canonical_assessment(request, **overrides):
    """A canonical assessment of a complete, passing review shape."""
    payload = {
        "slot_id": request.slot_id,
        "attempt": request.attempt,
        "scores": passing_scores(request),
        "reasons": criterion_reasons(request),
        "hard_fail": {key: False for key in request.post_type.review.hard_fail},
    }
    payload.update(overrides)

    return ReviewAssessment.model_validate(payload)


def wire_assessment(request, **overrides):
    """A raw reviewer payload validated through the request-specific wire schema."""
    payload = {
        "slot_id": request.slot_id,
        "attempt": request.attempt,
        "scores": passing_scores(request),
        "reasons": criterion_reasons(request),
        "hard_fail": {key: False for key in request.post_type.review.hard_fail},
    }
    payload.update(overrides)

    return review_wire_schema(request).model_validate(payload)


def malformed_assessments(request):
    """One broken assessment per way the rubric contract can be violated."""
    complete = canonical_assessment(request)
    criteria = request.rubric.criteria
    flagged = request.post_type.review.hard_fail[0]
    missing_scores = dict(complete.scores)
    missing_scores.pop(criteria[0])
    missing_flags = dict(complete.hard_fail)
    missing_flags.pop(flagged)
    unexpected_scores = {
        key: value for key, value in complete.scores.items() if key != criteria[-1]
    }
    unexpected_scores["clearity"] = 4.0

    return {
        "missing-scores": {"scores": missing_scores},
        "missing-hard-fail": {"hard_fail": missing_flags},
        "missing-reason": {
            "hard_fail": {key: key == flagged for key in complete.hard_fail},
            "reasons": {**criterion_reasons(request), flagged: " "},
        },
        "unexpected-field": {"scores": unexpected_scores},
        "out-of-range": {"scores": {**complete.scores, criteria[0]: 6.0}},
        "identity-slot": {"slot_id": "other"},
        "identity-attempt": {"attempt": request.attempt + 1},
    }


def test_public_judgement_accepts_a_complete_assessment():
    request = review_request()
    judgement = judge_review(request, canonical_assessment(request))

    assert judgement.verdict == "accepted"
    assert judgement.errors == []
    assert judgement.review.accepted is True
    assert judgement.review.hard_fail == []
    assert judgement.review.scores == passing_scores(request)
    assert score_review(request, canonical_assessment(request)) == judgement.review


def test_public_judgement_rejects_scores_below_the_pass_average_with_their_reasons():
    request = review_request()
    reasons = {key: "This criterion is too weak." for key in request.rubric.criteria}
    weak = canonical_assessment(request, scores=passing_scores(request, 2.0), reasons=reasons)

    judgement = judge_review(request, weak)

    assert judgement.verdict == "rejected"
    assert judgement.errors == []
    assert judgement.review.accepted is False
    assert judgement.review.hard_fail == []
    assert judgement.review.reasons == ["This criterion is too weak."]
    assert score_review(request, weak) == judgement.review


def test_a_reason_shared_by_a_criterion_and_a_hard_fail_is_listed_once():
    request = review_request()
    shared = "The claim is absent from the supplied evidence."
    flagged = "unsupported_claim"
    reasons = {**criterion_reasons(request), "grounding": shared, flagged: shared}
    scores = {**passing_scores(request), "grounding": 1.0}
    assessment = canonical_assessment(
        request,
        scores=scores,
        hard_fail={key: key == flagged for key in request.post_type.review.hard_fail},
        reasons=reasons,
    )

    review = judge_review(request, assessment).review

    assert review.reasons == [shared]


def test_public_judgement_rejects_a_hard_fail_flag_with_its_reason():
    request = review_request()
    flagged = "unsupported_claim"
    reason = "The claim is absent from the supplied evidence."
    flagged_assessment = canonical_assessment(
        request,
        hard_fail={key: key == flagged for key in request.post_type.review.hard_fail},
        reasons={**criterion_reasons(request), flagged: reason},
    )

    judgement = judge_review(request, flagged_assessment)

    assert judgement.verdict == "rejected"
    assert judgement.review.accepted is False
    assert judgement.review.hard_fail == [flagged]
    assert judgement.review.reasons == [reason]


def test_accepted_review_keeps_the_reason_of_a_below_average_criterion():
    request = review_request()
    weak = request.rubric.criteria[0]
    borderline = canonical_assessment(
        request,
        scores={**passing_scores(request), weak: 3.0},
        reasons={**criterion_reasons(request), weak: "Plain but acceptable."},
    )

    judgement = judge_review(request, borderline)

    assert judgement.verdict == "accepted"
    assert judgement.review.accepted is True
    assert judgement.review.reasons == ["Plain but acceptable."]


def test_one_criterion_below_the_floor_rejects_despite_a_passing_average():
    request = review_request()
    weak = request.rubric.criteria[-1]
    lopsided = canonical_assessment(
        request,
        scores={**passing_scores(request, 5.0), weak: 2.0},
        reasons={**criterion_reasons(request), weak: "No next step for the reader."},
    )

    judgement = judge_review(request, lopsided)

    assert request.post_type.review.min_score == 3
    assert judgement.verdict == "rejected"
    assert judgement.review.accepted is False
    assert judgement.review.reasons == ["No next step for the reader."]


@pytest.mark.parametrize("missing", ["scores", "hard_fail"])
def test_public_judgement_is_malformed_when_a_rubric_key_is_missing(missing):
    request = review_request()
    complete = canonical_assessment(request)
    values = dict(getattr(complete, missing))
    keys = request.rubric.criteria if missing == "scores" else request.post_type.review.hard_fail
    dropped = keys[0]
    values.pop(dropped)
    incomplete = canonical_assessment(request, **{missing: values})

    judgement = judge_review(request, incomplete)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert judgement.errors == [{"type": "missing", "loc": (missing, dropped), "input": None}]

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, incomplete)


def test_public_judgement_is_malformed_when_a_required_reason_is_blank():
    request = review_request()
    flagged = request.post_type.review.hard_fail[0]
    unreasoned = canonical_assessment(
        request,
        hard_fail={key: key == flagged for key in request.post_type.review.hard_fail},
        reasons={**criterion_reasons(request), flagged: "   "},
    )

    judgement = judge_review(request, unreasoned)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert judgement.errors == [{"type": "missing", "loc": ("reasons", flagged), "input": None}]

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, unreasoned)


def test_public_judgement_is_malformed_when_a_criterion_reason_is_missing():
    request = review_request()
    explained = request.rubric.criteria[0]
    ungrounded = canonical_assessment(request, reasons={explained: "Only one criterion explained."})

    judgement = judge_review(request, ungrounded)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert sorted(error["loc"] for error in judgement.errors) == sorted(
        ("reasons", key) for key in request.rubric.criteria[1:]
    )

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, ungrounded)


def test_public_judgement_is_malformed_for_an_unexpected_rubric_field():
    request = review_request()
    dropped = request.rubric.criteria[-1]
    misspelled = {
        **{key: value for key, value in passing_scores(request).items() if key != dropped},
        "clearity": 4.0,
    }
    typo = canonical_assessment(request, scores=misspelled)

    judgement = judge_review(request, typo)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert [error["type"] for error in judgement.errors] == ["missing", "value_error"]
    assert judgement.errors[0] == {"type": "missing", "loc": ("scores", dropped), "input": None}
    assert judgement.errors[1]["loc"] == ("scores",)
    assert str(judgement.errors[1]["ctx"]["error"]) == "unexpected rubric fields"

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, typo)


def test_public_judgement_is_malformed_for_a_score_outside_one_to_five():
    request = review_request()
    out_of_range = request.rubric.criteria[0]
    oversized = canonical_assessment(request, scores={**passing_scores(request), out_of_range: 6.0})

    judgement = judge_review(request, oversized)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert [error["type"] for error in judgement.errors] == ["value_error"]
    assert judgement.errors[0]["loc"] == ("scores", out_of_range)
    assert str(judgement.errors[0]["ctx"]["error"]) == "score outside 1..5"

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, oversized)


@pytest.mark.parametrize("field,value", [("slot_id", "other"), ("attempt", 2)])
def test_public_judgement_is_malformed_for_an_identity_mismatch(field, value):
    request = review_request()
    mismatched = canonical_assessment(request, **{field: value})

    judgement = judge_review(request, mismatched)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert [error["loc"] for error in judgement.errors] == [(field,)]
    assert str(judgement.errors[0]["ctx"]["error"]) == "identity mismatch"

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, mismatched)


@pytest.mark.parametrize(
    "name",
    [
        "missing-scores",
        "missing-hard-fail",
        "missing-reason",
        "unexpected-field",
        "out-of-range",
        "identity-slot",
        "identity-attempt",
    ],
)
def test_malformed_review_never_scores_and_never_approves(name):
    """DESIGN 6.10: a malformed review fails closed instead of becoming approval."""
    request = review_request()
    malformed = canonical_assessment(request, **malformed_assessments(request)[name])

    judgement = judge_review(request, malformed)

    assert judgement.verdict == "malformed"
    assert judgement.review is None
    assert judgement.errors

    with pytest.raises(ModelFailure, match="failed_output"):
        score_review(request, malformed)


def test_wire_schema_requires_every_score_and_hard_fail_key():
    request = review_request()

    with pytest.raises(ValidationError):
        wire_assessment(request, scores={})

    with pytest.raises(ValidationError):
        wire_assessment(request, hard_fail={})


def test_wire_schema_bounds_scores_to_the_rubric_range():
    request = review_request()
    criterion = request.rubric.criteria[0]

    with pytest.raises(ValidationError):
        wire_assessment(request, scores={**passing_scores(request), criterion: 0.5})


def test_wire_schema_uses_the_rubric_aliases_and_rejects_unknown_keys():
    request = review_request()
    payload = wire_assessment(request).model_dump(by_alias=True)

    assert set(payload["scores"]) == set(request.rubric.criteria)
    assert set(payload["hard_fail"]) == set(request.post_type.review.hard_fail)

    with pytest.raises(ValidationError):
        wire_assessment(request, scores={**passing_scores(request), "clearity": 4.0})


def test_wire_assessment_canonicalises_to_the_same_judgement():
    request = review_request()
    canonical = canonical_review(wire_assessment(request))

    assert canonical == canonical_assessment(request)
    assert judge_review(request, canonical).verdict == "accepted"


def test_legacy_import_names_still_reach_the_scoring_module():
    from content_pipeline import agents, review_scoring, review_wire

    assert agents.score_review is review_scoring.score_review
    assert agents.review_contract_errors is review_scoring.review_contract_errors
    assert review_wire.review_wire_schema is review_scoring.review_wire_schema
    assert review_wire.canonical_review is review_scoring.canonical_review

    request = review_request()
    complete = canonical_assessment(request)

    assert agents.review_contract_errors(request, complete) == []
    assert agents.score_review(request, complete).accepted is True


def test_review_floor_cannot_exceed_pass_average():
    with pytest.raises(ValidationError, match="min_score"):
        ReviewConfig(pass_avg=3.5, min_score=4, hard_fail=[])

    assert ReviewConfig(pass_avg=3.5, min_score=3, hard_fail=[]).min_score == 3
