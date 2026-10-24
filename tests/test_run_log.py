"""run.log rendering from hand-built trail files, and the run folder index."""

import json

from content_pipeline.run_log import render_run_index, render_run_log


def write(run_dir, name, records):
    (run_dir / name).write_text(json.dumps(records), encoding="utf-8")


def research(slot_id, attempt, action="finish", query="", evidence=(), pack=None, failure=None):
    """A research record with the real field names."""
    return {
        "record_id": f"{slot_id}/research/{attempt}/1",
        "slot_id": slot_id,
        "attempt": attempt,
        "pack": pack,
        "evidence": list(evidence),
        "action": action,
        "query": query,
        "failure": failure,
    }


def draft(slot_id, attempt, tweets, accepted=True, violations=(), fixes=()):
    """A draft record whose audit.normalized is a list of tweets."""
    return {
        "slot_id": slot_id,
        "attempt": attempt,
        "failure": None,
        "audit": {
            "accepted": accepted,
            "violations": list(violations),
            "fixes": list(fixes),
            "normalized": [{"text": text, "source_ids": ["SS-01"]} for text in tweets],
        },
    }


def review(slot_id, attempt, accepted, scores, reasons=(), hard_fail=()):
    return {
        "slot_id": slot_id,
        "attempt": attempt,
        "failure": None,
        "review": {
            "scores": scores,
            "reasons": list(reasons),
            "hard_fail": list(hard_fail),
            "accepted": accepted,
        },
    }


def summary():
    return {
        "slots": {
            "thread_1": {"outcome": "shipped", "attempts": 2, "reasons": []},
            "thread_2": {
                "outcome": "dropped_quality",
                "attempts": 2,
                "reasons": ["hook too weak", "claim lacks conditions"],
            },
        },
        "shipped": 1,
        "confirmed_tokens": 900,
        "uncertain_tokens": 100,
        "confirmed_cost_usd": 0.01,
        "uncertain_cost_usd": 0.002,
        "unpriced": False,
        "advisor": "disabled",
    }


def build(tmp_path):
    """Write a rejected-then-shipped slot and a dropped slot, and render the log."""
    evidence = [
        {"source_id": "SS-01", "status": "ok"},
        {"source_id": "WEB-1", "status": "failed"},
    ]
    pack = {
        "topic_id": "SS-01",
        "topic_name": "Reentrancy",
        "sources": [{"source_id": "SS-01"}],
    }

    write(
        tmp_path,
        "research_log.json",
        [
            research("thread_1", 1, action="search_kb", query="reentrancy", evidence=evidence),
            research("thread_1", 1, pack=pack),
            research("thread_2", 1, failure="no evidence"),
        ],
    )

    write(
        tmp_path,
        "drafts.json",
        [
            draft("thread_1", 1, ["a", "b"]),
            draft("thread_1", 2, ["a", "b", "c"], fixes=["dash"]),
            draft("thread_2", 1, ["a"], accepted=False, violations=["too long"]),
        ],
    )

    write(
        tmp_path,
        "review_report.json",
        [
            review(
                "thread_1",
                1,
                False,
                {"hook": 2, "voice": 4.5},
                reasons=["opens with a question"],
                hard_fail=["bad_claim"],
            ),
            review("thread_1", 2, True, {"hook": 4, "voice": 5}),
        ],
    )

    return render_run_log(tmp_path, summary())


def test_rejected_then_shipped_slot_shows_both_attempts(tmp_path):
    log = build(tmp_path)
    block = log.split("== thread_2")[0]

    assert "review 1   REJECTED  hook=2 voice=4.5" in block
    assert "reason: opens with a question" in block
    assert "hard fail: bad_claim" in block
    assert "draft 2    3 tweets, rules ok, fixed dash" in block
    assert "review 2   accepted  hook=4 voice=5" in block
    assert block.index("REJECTED") < block.index("review 2")
    assert "outcome    shipped after 2 attempt(s)\n" in block


def test_search_and_failed_research_records_are_logged(tmp_path):
    log = build(tmp_path)

    assert "research   search_kb 'reentrancy' -> SS-01:ok, WEB-1:failed" in log
    assert "research   topic SS-01 (Reentrancy) from SS-01" in log
    assert "research failed: no evidence" in log


def test_dropped_slot_lists_reasons_and_totals(tmp_path):
    log = build(tmp_path)
    outcome = "outcome    dropped_quality after 2 attempt(s): hook too weak; claim lacks conditions"

    assert "rules FAILED: too long" in log
    assert outcome in log
    assert "shipped 1, dropped or failed 1" in log
    assert "tokens 1000, LLM cost $0.0120" in log
    assert "advisor disabled" in log


def test_dropped_slot_points_to_the_review_instead_of_repeating_its_reasons(tmp_path):
    reasons = ["hook too weak", "claim lacks conditions"]
    drop = {"outcome": "dropped_quality", "attempts": 2, "reasons": reasons}

    write(tmp_path, "review_report.json", [review("a", 2, False, {"hook": 2}, reasons=reasons)])

    log = render_run_log(tmp_path, {**summary(), "slots": {"a": drop}})

    assert "outcome    dropped_quality after 2 attempt(s), see review 2 reasons above" in log
    assert log.count("hook too weak") == 1


def test_writer_failure_keeps_its_reasons_and_shows_the_failed_check(tmp_path):
    failed = draft("a", 2, [])
    failed.update(audit=None, failure="failed_output", failure_detail="slot_id_mismatch")
    drop = {"outcome": "dropped_quality", "attempts": 2, "reasons": ["writer:failed_output"]}

    write(tmp_path, "drafts.json", [failed])
    write(tmp_path, "review_report.json", [review("a", 1, False, {"hook": 2}, reasons=["other"])])

    log = render_run_log(tmp_path, {**summary(), "slots": {"a": drop}})

    assert "draft 2    failed: failed_output (slot_id_mismatch)" in log
    assert "attempt(s): writer:failed_output" in log


def test_run_index_lists_existing_files_only(tmp_path):
    (tmp_path / "posts.csv").write_text("", encoding="utf-8")
    (tmp_path / "run.log").write_text("", encoding="utf-8")

    index = render_run_index(tmp_path)

    assert "`posts.csv`" in index
    assert "`run.log`" in index
    assert "ledger.json" not in index
    assert "run.lock" in index.splitlines()[-1]
    assert "not listed" in index
