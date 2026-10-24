"""Record one finished live week as the offline replay fixture.

The replay test re-runs the current code checks over these real drafts and
research packs, so a check that would now reject (or accept) a real post fails
the suite in seconds without spending money. Record a fresh week after a
deliberate prompt, model, policy or check change:

    python run_week.py --plan inputs/plan.json --output output/live-replay-week
    python scripts/record_live_week.py output/live-replay-week

Only the parts the replay needs are kept: the resolved plan, every recorded
draft and its audit, and each slot's research pack. Image paths, error strings
and usage files stay out. The script refuses to write a fixture that still
contains a key-like string or a local absolute path.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests" / "fixtures" / "live_week"
AGENTS = ("research", "thread_writer", "marketing_writer", "reviewer", "visual")
SECRET = re.compile(r"(?:sk-ant[-_A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}|tvly-[A-Za-z0-9]{8,})")
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/|AppData")


def read(folder: Path, name: str):
    """Load one JSON file from the run folder."""
    return json.loads((folder / name).read_text(encoding="utf-8"))


def commit():
    """Return the checked-out commit, or None outside a git checkout."""
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def model_names():
    """Return the model each agent was configured with."""
    models = {}

    for agent in AGENTS:
        path = ROOT / "agents" / agent / "agent.yaml"

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("model:"):
                models[agent] = line.split(":", 1)[1].strip()
                break

    return models


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


def check_clean(payload):
    """Refuse to commit a key-like string or a local path in the fixture."""
    text = json.dumps(payload, ensure_ascii=False)

    for pattern, what in ((SECRET, "a key-like string"), (LOCAL_PATH, "a local absolute path")):
        match = pattern.search(text)

        if match:
            raise SystemExit(f"refusing to record: {what} ({match.group(0)!r})")


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
        "commit": commit(),
        "run_id": summary["run_id"],
        "models": model_names(),
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
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    print(
        f"recorded {len(payload['drafts'])} drafts over {len(payload['research'])} slots "
        f"({len(week['shipped'])} shipped) into {DEST.relative_to(ROOT).as_posix()}"
    )


if __name__ == "__main__":
    main()
