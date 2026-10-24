"""Replay one recorded live week through the current code checks, offline.

The scripted-model suite proves routing and rules; it cannot show what a real
model writes. This replay holds real drafts and research from a live Claude
week (`scripts/record_live_week.py`) and re-runs the checks over them, so a
check that would now reject an acceptable post, or pass a broken one, fails in
seconds instead of after another paid run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_pipeline.models import Draft, ResearchPack, ResolvedSlot
from content_pipeline.post_types import PostTypes
from content_pipeline.validation import validate

ROOT = Path(__file__).parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "live_week"


def recorded(name):
    return json.loads((FIXTURE / f"{name}.json").read_text(encoding="utf-8"))


def week():
    return recorded("week")


def slot(slot_id):
    return next(item for item in week()["slots"] if item["slot_id"] == slot_id)


def research(slot_id):
    return recorded("research")[slot_id]


def resolved(item):
    return ResolvedSlot(
        slot_id=item["slot_id"],
        type=item["type"],
        focus=item["focus"],
        format=item["format"],
        service_line=item["service_line"],
        service_lines=item["service_lines"],
        cta_allowed=item["cta_allowed"],
    )


def run_checks(record):
    """Run the current code checks over one recorded draft."""
    item = slot(record["slot_id"])
    types = PostTypes(ROOT)
    rules, _ = types.brand()
    plan = resolved(item)

    return validate(
        Draft.model_validate(record["audit"]["original"]),
        ResearchPack.model_validate(research(record["slot_id"])),
        types.spec(item["type"]),
        rules,
        week()["settings"]["max_tweet_chars"],
        item["focus"],
        ai_policy_root=ROOT,
        format=types.format(plan),
        cta_allowed=item["cta_allowed"],
        service_lines=item["service_lines"],
    )


DRAFTS = {record["record_id"]: record for record in recorded("drafts")}


@pytest.mark.parametrize("record_id", list(DRAFTS))
def test_a_recorded_draft_gets_exactly_the_audit_it_got_in_the_live_run(record_id):
    record = DRAFTS[record_id]
    audit = record["audit"]
    result = run_checks(record)

    assert result.accepted is audit["accepted"], record_id
    assert result.violations == audit["violations"], record_id
    assert result.fixes == audit["fixes"], record_id
    assert [tweet.text for tweet in result.normalized.tweets] == [
        tweet["text"] for tweet in audit["normalized"]
    ], record_id


def test_every_recorded_shipped_post_still_passes_every_code_check():
    shipped = week()["shipped"]
    assert shipped, "the recording has no shipped post to replay"

    for slot_id in shipped:
        attempts = [record for record in DRAFTS.values() if record["slot_id"] == slot_id]
        last = max(attempts, key=lambda record: record["attempt"])

        assert run_checks(last).accepted, (
            f"{slot_id} shipped in the live week but the current checks reject it; "
            "fix the check or re-record the week"
        )


def test_the_recording_holds_a_rejection_a_retry_and_a_drop():
    outcomes = [item["outcome"] for item in week()["slots"]]
    attempts = [item["attempt"] for item in week()["slots"]]

    assert any(not record["audit"]["accepted"] for record in DRAFTS.values()), "no rejected draft"
    assert any(count > 1 for count in attempts), "no retried slot"
    assert any(outcome != "shipped" for outcome in outcomes), "no dropped slot"


@pytest.mark.parametrize(
    "prompt",
    sorted(path.relative_to(ROOT).as_posix() for path in ROOT.glob("post_types/*/writer.md")),
)
def test_each_writer_prompt_states_its_tweet_budget(prompt):
    text = (ROOT / prompt).read_text(encoding="utf-8").casefold()

    assert "`max_tweet_chars`" in text, f"{prompt} lost its tweet length limit"
    assert "budget" in text, f"{prompt} lost its length budget"


def test_the_recorded_week_was_written_by_the_committed_models():
    models = week()["models"]

    assert models["thread_writer"] == models["marketing_writer"]
    assert models["reviewer"] != models["thread_writer"]
    assert week()["settings"]["max_tweet_chars"] == 280
