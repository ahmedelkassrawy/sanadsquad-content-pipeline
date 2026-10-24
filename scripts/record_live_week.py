"""Record one finished live week as the offline replay fixture.

The replay test re-runs the current code checks over these real drafts and
research packs, so a check that would now reject (or accept) a real post fails
the suite in seconds without spending money. Record a fresh week only when a
replay failure showed the recording was wrong, not the check:

    python run_week.py --plan inputs/plan.json --output output/live-replay-week
    python scripts/record_live_week.py output/live-replay-week

Only the parts the replay needs are kept: the resolved plan, every recorded
draft and its audit, and each slot's research pack. Image paths, error strings
and usage files stay out. The script refuses to write a fixture that still
contains a key-like string, a value from the local `.env`, or a local
absolute path. `recorded_at` and `cost_usd` change on every recording;
everything else should not.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests" / "fixtures" / "live_week"
# The prompt and policy files, as they were inside the run folder, that shaped the drafts.
SNAPSHOT = (
    "agents/research/system.md",
    "agents/thread_writer/system.md",
    "agents/marketing_writer/system.md",
    "agents/reviewer/system.md",
    "post_types/tech-thread/writer.md",
    "post_types/tech-thread/rubric.md",
    "post_types/marketing-post/writer.md",
    "post_types/marketing-post/rubric.md",
    "inputs/ai-constructions.yaml",
    "inputs/ai-vocabulary.txt",
)
# The pipeline's own redaction treats any 28-character token as suspicious, which is right for
# an error string but would reject page text and content hashes here. Match credential prefixes,
# then check every value configured in the local .env.
SECRET = re.compile(r"(?:sk|nvapi|gsk|AIza|tvly)[-_A-Za-z0-9]{8,}")
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/|/root/|AppData")


def read(folder: Path, name: str):
    """Load one JSON file from the run folder."""
    return json.loads((folder / name).read_text(encoding="utf-8"))


def snapshot_digests(folder):
    """Return the sha256 of each prompt and policy file the run actually used."""
    return {
        name: hashlib.sha256((folder / "snapshot" / name).read_bytes()).hexdigest()
        for name in SNAPSHOT
        if (folder / "snapshot" / name).exists()
    }


def model_names(folder):
    """Return the model each agent used, read from the run's own usage log."""
    names = {}

    for line in (folder / "usage.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        node = row.get("node") or ""
        names.setdefault("research" if node.startswith("research") else node, row.get("model"))

    if not names:
        raise SystemExit("the run folder has no usage records; cannot record model provenance")

    return {name: names[name] for name in sorted(names)}


def research_packs(states):
    """Keep each slot's research pack, the input every draft was written from."""
    return {
        slot_id: state["pack"] for slot_id, state in states["slots"].items() if state.get("pack")
    }


def slots(states, summary):
    """Keep the planning fields the replay needs; drop outcomes' free text."""
    kept = []

    for slot_id in summary["slots"]:
        state = states["slots"][slot_id]
        slot = state["slot"]

        kept.append(
            {
                "slot_id": slot_id,
                "type": slot["type"],
                "focus": slot.get("focus"),
                "format": slot.get("format"),
                "service_line": slot.get("service_line"),
                "service_lines": slot.get("service_lines"),
                "cta_allowed": slot.get("cta_allowed", True),
                "attempt": state.get("attempt", 0),
                "outcome": state.get("outcome"),
                "topic_id": slot.get("topic_id"),
            }
        )

    return kept


def drafts(records):
    """Keep every recorded draft and the audit the checks produced for it."""
    kept = []

    for record in records:
        audit = record.get("audit")

        if audit is None or record.get("failure"):
            continue

        kept.append(
            {
                "record_id": record["record_id"],
                "slot_id": record["slot_id"],
                "attempt": record["attempt"],
                "audit": audit,
            }
        )

    return kept


def configured_secrets():
    """Return the values in the local .env, so a leak of any of them is caught."""
    path = ROOT / ".env"
    if not path.exists():
        return set()

    values = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        value = value.strip().strip("'\"")
        if separator and not name.strip().startswith("#") and len(value) >= 12:
            values.add(value)

    return values


def plan_week(folder):
    """Return the week the marketing mix used, when the plan pinned one."""
    path = folder / "snapshot" / "inputs" / "plan.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig")).get("week")


def check_clean(payload):
    """Refuse to write a key, a configured secret value or a local path into the fixture."""
    text = json.dumps(payload, ensure_ascii=False)

    for pattern, what in ((SECRET, "a key-like string"), (LOCAL_PATH, "a local absolute path")):
        match = pattern.search(text)

        if match:
            raise SystemExit(f"refusing to record: {what} ({match.group(0)!r})")

    if any(value in text for value in configured_secrets()):
        raise SystemExit("refusing to record: a value from .env appears in the run")


def main(argv=None):
    """Write the fixture from one run folder and print what was kept."""
    argv = sys.argv[1:] if argv is None else argv

    if len(argv) != 1:
        raise SystemExit("usage: record_live_week.py <run-folder>")

    folder = Path(argv[0]).resolve()
    states = read(folder, "state.json")
    summary = read(folder, "run_summary.json")
    settings = read(folder, "runtime-settings.json")

    week = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_sha256": snapshot_digests(folder),
        "run_id": summary["run_id"],
        "models": model_names(folder),
        "marketing_week": plan_week(folder),
        "settings": {
            "max_tweet_chars": settings["max_tweet_chars"],
        },
        "shipped": [
            slot for slot, value in summary["slots"].items() if value["outcome"] == "shipped"
        ],
        "cost_usd": summary["confirmed_cost_usd"],
        "slots": slots(states, summary),
    }

    packs = research_packs(states)
    payload = {
        "week": week,
        "research": packs,
        # A slot whose research failed has no pack, so it has nothing to replay.
        "drafts": [
            record for record in drafts(read(folder, "drafts.json")) if record["slot_id"] in packs
        ],
    }

    check_clean(payload)

    DEST.mkdir(parents=True, exist_ok=True)

    for name, value in payload.items():
        (DEST / (name + ".json")).write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            # .gitattributes keeps this folder byte-for-byte, so pin the line ending here
            # rather than depending on the platform that re-records the week.
            newline="\r\n",
        )

    print(
        f"recorded {len(payload['drafts'])} drafts over {len(payload['research'])} slots "
        f"({len(week['shipped'])} shipped) into {DEST.relative_to(ROOT).as_posix()}"
    )


if __name__ == "__main__":
    main()
