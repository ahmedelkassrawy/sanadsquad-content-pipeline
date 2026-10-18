"""Per-agent token and cost totals in run_summary.json and run.log."""

import asyncio
import json
from types import SimpleNamespace

from content_pipeline.contracts import ExternalUsage, RunSummary
from content_pipeline.ledger import Ledger, agent_usage
from content_pipeline.pipeline import Pipeline
from content_pipeline.run_log import RUN_FILES, render_run_log


def row(node, tokens_in, tokens_out, cost, status="confirmed"):
    return {
        "call_id": f"thread_1/{node}/1/1",
        "slot_id": "thread_1",
        "node": node,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost,
        "cost_source": "unknown" if cost is None else "genai-prices",
        "status": status,
    }


ROWS = [
    row("research_step_1", 1000, 100, 0.01),
    row("research_step_2", 2000, 200, 0.02),
    row("research_topics", 500, 50, 0.005),
    row("writer", 3000, 600, 0.04),
    row("writer", 3100, 650, 0.041, status="uncertain"),
    row("reviewer", 4000, 300, 0.05),
    row("visual", 400, 40, 0.004),
    row("advisor", 900, 90, 0.009),
]


def summary_for(tmp_path, rows):
    ledger = Ledger(tmp_path / "ledger.json", 10**9, 100.0)

    async def charge():
        for index, usage in enumerate(rows):
            await ledger.reserve(str(index), 1, 0.0)
            await ledger.dispatch(str(index))

            if usage["status"] == "uncertain":
                await ledger.uncertain(str(index))
            else:
                await ledger.settle(
                    str(index), usage["input_tokens"] + usage["output_tokens"], usage["cost_usd"]
                )

    asyncio.run(charge())

    (tmp_path / "usage.jsonl").write_text(
        "".join(json.dumps(usage) + "\n" for usage in rows), encoding="utf-8"
    )

    # The uncertain call keeps its reservation size in the ledger, so mirror its real charge.
    for index, usage in enumerate(rows):
        if usage["status"] == "uncertain":
            ledger.entries[str(index)] = {
                "tokens": usage["input_tokens"] + usage["output_tokens"],
                "cost_usd": usage["cost_usd"],
                "status": "uncertain",
            }

    fake = SimpleNamespace(
        context=SimpleNamespace(ledger=ledger, run_dir=tmp_path, identity=None),
        external=ExternalUsage(),
        _terminal_slots=lambda states: {},
    )

    return Pipeline._build_summary(fake, [], {"advisor": "completed"})


def test_rows_are_grouped_by_agent_with_research_steps_merged():
    agents = agent_usage(ROWS)

    assert list(agents) == ["research", "writer", "reviewer", "visual", "advisor"]
    assert agents["research"]["calls"] == 3
    assert agents["research"]["tokens"] == 3850
    assert round(agents["research"]["cost_usd"], 6) == 0.035
    assert agents["writer"]["calls"] == 2
    assert agents["writer"]["uncertain_tokens"] == 3750
    assert round(agents["writer"]["uncertain_cost_usd"], 6) == 0.041
    assert agents["reviewer"]["uncertain_tokens"] == 0


def test_unpriced_rows_count_zero_cost():
    agents = agent_usage([row("reviewer", 10, 5, None)])

    assert agents["reviewer"] == {
        "calls": 1,
        "tokens": 15,
        "cost_usd": 0.0,
        "uncertain_tokens": 0,
        "uncertain_cost_usd": 0.0,
    }


def test_agent_totals_add_up_to_the_run_totals(tmp_path):
    summary = summary_for(tmp_path, ROWS)
    agents = summary.agents

    run_tokens = summary.confirmed_tokens + summary.uncertain_tokens
    run_cost = summary.confirmed_cost_usd + summary.uncertain_cost_usd

    assert sum(agent.calls for agent in agents.values()) == len(ROWS)
    assert sum(agent.tokens for agent in agents.values()) == run_tokens
    assert round(sum(agent.cost_usd for agent in agents.values()), 6) == round(run_cost, 6)
    assert sum(agent.uncertain_tokens for agent in agents.values()) == summary.uncertain_tokens
    assert agents["writer"].tokens == 3600 + 3750


def test_summary_without_agents_still_loads():
    summary = RunSummary.model_validate_json(
        '{"record_id": "_run/summary/1", "slot_id": "_run", "attempt": 1, "run_id": "r",'
        ' "slots": {}, "shipped": 0, "confirmed_tokens": 0, "uncertain_tokens": 0,'
        ' "confirmed_cost_usd": 0, "uncertain_cost_usd": 0, "unpriced": false,'
        ' "external_usage": {}}'
    )

    assert summary.agents == {}


def test_run_log_prints_one_line_per_agent(tmp_path):
    summary = summary_for(tmp_path, ROWS)

    log = render_run_log(tmp_path, summary.model_dump(mode="json"))
    lines = log.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("tokens "))

    assert [line.split()[0] for line in lines[start + 1 : start + 6]] == [
        "research",
        "writer",
        "reviewer",
        "visual",
        "advisor",
    ]
    assert "writer    2 call(s), 7350 tokens, $0.0810" in log


def test_file_descriptions_claim_only_what_exists():
    about = dict(RUN_FILES)

    assert "per slot and agent" not in about["ledger.json"]
    assert "per agent" in about["run_summary.json"]
