import asyncio
import json
from pathlib import Path


class BudgetExhausted(Exception):
    pass


def agent_name(node: str) -> str:
    """The agent behind a usage row's node: every research step counts as research."""
    return "research" if node.startswith("research") else node


def agent_usage(rows: list[dict]) -> dict[str, dict]:
    """Sum usage.jsonl rows per agent: calls, tokens (input plus output) and USD.

    Rows with status "uncertain" or "dispatched" are also reported separately, matching how the
    run totals split confirmed from uncertain charges.
    """
    agents: dict[str, dict] = {}

    for row in rows:
        total = agents.setdefault(
            agent_name(row["node"]),
            {
                "calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "uncertain_tokens": 0,
                "uncertain_cost_usd": 0.0,
            },
        )

        tokens = row["input_tokens"] + row["output_tokens"]
        cost = row["cost_usd"] or 0.0

        total["calls"] += 1
        total["tokens"] += tokens
        total["cost_usd"] += cost

        if row["status"] in {"uncertain", "dispatched"}:
            total["uncertain_tokens"] += tokens
            total["uncertain_cost_usd"] += cost

    return agents


class Ledger:
    def __init__(self, path: Path, token_budget: int, cost_budget: float):
        self.path = path
        self.token_budget = token_budget
        self.cost_budget = cost_budget
        self.lock = asyncio.Lock()
        self.entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

        for record in self.entries.values():
            if record["status"] == "reserved":
                record["status"] = "released"
            elif record["status"] == "dispatched":
                record["status"] = "uncertain"

        self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temp = self.path.with_suffix(".tmp")

        temp.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def totals(self):
        active = [r for r in self.entries.values() if r["status"] != "released"]

        return {
            "tokens": sum(r["tokens"] for r in active),
            "cost_usd": sum(r["cost_usd"] or 0 for r in active),
            "uncertain_tokens": sum(
                r["tokens"] for r in active if r["status"] in {"uncertain", "dispatched"}
            ),
            "uncertain_cost_usd": sum(
                r["cost_usd"] or 0 for r in active if r["status"] in {"uncertain", "dispatched"}
            ),
        }

    async def reserve(self, call_id: str, tokens: int, cost: float | None):
        async with self.lock:
            if call_id in self.entries and self.entries[call_id]["status"] != "released":
                raise ValueError("call id already charged; use next call_n")

            total = self.totals()

            if (
                total["tokens"] + tokens > self.token_budget
                or total["cost_usd"] + (cost or 0) > self.cost_budget
            ):
                raise BudgetExhausted("failed_budget")

            self.entries[call_id] = {"tokens": tokens, "cost_usd": cost, "status": "reserved"}

            self._save()

    async def dispatch(self, call_id):
        async with self.lock:
            self.entries[call_id]["status"] = "dispatched"

            self._save()

    async def settle(self, call_id, tokens, cost):
        async with self.lock:
            self.entries[call_id] = {"tokens": tokens, "cost_usd": cost, "status": "confirmed"}

            self._save()

    async def uncertain(self, call_id):
        async with self.lock:
            self.entries[call_id]["status"] = "uncertain"

            self._save()

    async def release(self, call_id):
        """Release a reservation after a definite provider refusal."""
        async with self.lock:
            self.entries[call_id]["status"] = "released"

            self._save()
