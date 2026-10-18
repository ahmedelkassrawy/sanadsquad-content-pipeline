"""Human-readable run.log, rebuilt from the trail files so it matches them exactly."""

import json
from pathlib import Path


def _records(run_dir: Path, name: str) -> list[dict]:
    path = run_dir / name

    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _research_lines(record: dict) -> list[str]:
    if record["failure"]:
        return [f"research failed: {record['failure']}"]

    if record["pack"]:
        pack = record["pack"]
        sources = ", ".join(s["source_id"] for s in pack["sources"])

        return [f"research   topic {pack['topic_id']} ({pack['topic_name']}) from {sources}"]

    found = ", ".join(f"{e['source_id']}:{e['status']}" for e in record["evidence"]) or "nothing"

    return [f"research   {record['action']} {record['query']!r} -> {found}"]


def _draft_lines(record: dict) -> list[str]:
    if record["failure"]:
        detail = f" ({record['failure_detail']})" if record.get("failure_detail") else ""

        return [f"draft {record['attempt']}    failed: {record['failure']}{detail}"]

    attempt = record["attempt"]
    audit = record["audit"]
    count = len(audit["normalized"])
    fixes = f", fixed {', '.join(audit['fixes'])}" if audit["fixes"] else ""

    if audit["accepted"]:
        return [f"draft {attempt}    {count} tweets, rules ok{fixes}"]

    violations = ", ".join(audit["violations"])

    return [f"draft {attempt}    {count} tweets, rules FAILED: {violations}"]


def _review_lines(record: dict) -> list[str]:
    if record["failure"]:
        return [f"review {record['attempt']}   failed: {record['failure']}"]

    review = record["review"]
    scores = " ".join(f"{k}={v:g}" for k, v in review["scores"].items())
    verdict = "accepted" if review["accepted"] else "REJECTED"
    lines = [f"review {record['attempt']}   {verdict}  {scores}"]

    if review["hard_fail"]:
        lines.append(f"           hard fail: {', '.join(review['hard_fail'])}")

    if not review["accepted"]:
        lines.extend(f"           reason: {reason}" for reason in review["reasons"])

    return lines


# Read in this order: the result first, then the trail behind it.
RUN_FILES = [
    ("posts.csv", "The deliverable: accepted posts (Post Text, Image) for a scheduler."),
    ("run.log", "What happened to each slot, step by step, and the totals."),
    ("run_summary.json", "Shipped, dropped and why, tokens and cost."),
    ("plan.resolved.json", "The plan after --set overrides."),
    ("research_log.json", "Every search and the evidence each slot was given."),
    ("drafts.json", "Every draft, the code fixes applied and any rule violations."),
    ("review_report.json", "Reviewer scores and reasons for every draft that reached review."),
    ("final.json", "The accepted pieces with topic, sources and image."),
    ("posts.json", "posts.csv as JSON."),
    ("visuals.json", "Image specs and how each image was made."),
    ("images/", "One image (and its spec) per accepted piece."),
    ("ledger.json", "Token and cost totals per slot and agent."),
    ("usage.jsonl", "One line per model call: model, tokens, cost."),
    ("external-usage.json", "Search, fetch and image calls."),
    ("advisor.json", "Optional advisor output: suggested instruction edits."),
    ("advice.md", "The advisor's suggestions in prose. Never applied for you."),
    ("advice.patch", "The advisor's suggestions as a patch. Never applied for you."),
    ("schemas/", "JSON Schemas for every file above."),
    ("snapshot/", "Copy of the inputs and instructions this run used."),
]


RESUME_FILES = (
    "checkpoint.sqlite, state.json, run_identity.json, runtime-settings.json, "
    "research-replay.json, run.lock"
)


def render_run_index(run_dir: Path) -> str:
    """A README listing the files this run folder actually contains."""
    rows = [
        f"| `{name}` | {about} |"
        for name, about in RUN_FILES
        if (run_dir / name.rstrip("/")).exists()
    ]

    lines = [
        "# Run folder",
        "",
        "Start with `posts.csv` and `run.log`.",
        "",
        "| File | What it holds |",
        "| --- | --- |",
    ]

    lines += rows
    lines += ["", f"Internal resume files ({RESUME_FILES}) are not listed."]

    return "\n".join(lines) + "\n"


def render_run_log(run_dir: Path, summary: dict) -> str:
    """One block per slot in plan order (research, drafts, reviews, outcome), then totals."""
    steps = {}
    reviewed = {}

    for name, render in [
        ("research_log.json", _research_lines),
        ("drafts.json", _draft_lines),
        ("review_report.json", _review_lines),
    ]:
        for record in _records(run_dir, name):
            if name == "review_report.json" and record["review"]:
                reviewed[record["slot_id"]] = record

            steps.setdefault(record["slot_id"], []).append(
                (record["attempt"], name, render(record))
            )

    lines = []

    for slot_id, slot in summary["slots"].items():
        lines.append(f"== {slot_id}")

        # Stable sort: attempt order first, then research, draft, review within an attempt.
        for _, _, entry in sorted(steps.get(slot_id, []), key=lambda step: step[0]):
            lines.extend("   " + line for line in entry)

        why = ""
        last = reviewed.get(slot_id)

        if slot["outcome"] != "shipped" and slot["reasons"]:
            if last and slot["reasons"] == last["review"]["reasons"]:
                why = f", see review {last['attempt']} reasons above"
            else:
                why = f": {'; '.join(slot['reasons'])}"

        outcome = f"{slot['outcome']} after {slot['attempts']} attempt(s){why}"

        lines.append(f"   outcome    {outcome}")

    dropped = len(summary["slots"]) - summary["shipped"]
    tokens = summary["confirmed_tokens"] + summary["uncertain_tokens"]
    cost = summary["confirmed_cost_usd"] + summary["uncertain_cost_usd"]
    priced = " (some calls unpriced)" if summary["unpriced"] else ""

    lines += [
        "",
        f"shipped {summary['shipped']}, dropped or failed {dropped}",
        f"tokens {tokens}, LLM cost ${cost:.4f}{priced}",
        f"advisor {summary['advisor']}",
    ]

    return "\n".join(lines) + "\n"
