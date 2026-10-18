"""Explicitly gated real-provider checks; no dispatch without --live."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from .agents import Reviewer, load_rubric
from .config import (
    Settings,
    load_agent,
    load_brand,
    load_plan,
    load_type,
    local_file,
    resolve_model,
)
from .contracts import ReviewRequest
from .durability import atomic_json
from .ledger import BudgetExhausted, Ledger
from .model_client import PRICES_FILE, Prices, configured_client, load_price_overrides
from .models import Draft, ResearchPack, Tweet
from .pipeline import run_pipeline, validate_inputs
from .research import KnowledgeBase
from .validation import validate


async def reviewer_control(
    root,
    output,
    model,
    token_budget,
    cost_budget,
    allow_unpriced=False,
    max_concurrency=None,
    reasoning_effort_override=None,
):
    settings = Settings(
        model_override=model,
        reasoning_effort_override=reasoning_effort_override,
        run_token_budget=token_budget,
        run_cost_budget_usd=cost_budget,
        advisor_enabled=False,
        allow_unpriced=allow_unpriced,
        max_concurrency=max_concurrency,
    )

    controls = json.loads(
        local_file(root, "config/live-review-controls.json").read_text(encoding="utf-8")
    )

    spec = load_type(root, controls["post_type"])
    kb = KnowledgeBase(root / "inputs/kb.json")
    source = kb.source(controls["topic_id"])

    rules, _ = load_brand(root / "inputs/brand-voice.md")

    prepared = []

    for name, text in [
        ("supported", controls["supported_text"]),
        ("unsupported", controls["unsupported_text"]),
    ]:
        pack = ResearchPack(
            slot_id=name, topic_id=source.source_id, topic_name=source.title, sources=[source]
        )

        draft = Draft(
            slot_id=name,
            tweets=[Tweet(text=text, source_ids=[source.source_id]) for _ in range(spec.units.min)],
        )

        checked = validate(draft, pack, spec, rules)

        if not checked.accepted:
            raise ValueError("live control does not pass code validation")

        prepared.append(
            (
                name,
                ReviewRequest(
                    slot_id=name,
                    attempt=1,
                    draft=checked.normalized,
                    research=pack,
                    post_type=spec,
                    rubric=load_rubric(root, spec),
                ),
            )
        )

    output.mkdir(parents=True, exist_ok=False)

    ledger = Ledger(output / "ledger.json", token_budget, cost_budget)
    prices = Prices(update=False, overrides=load_price_overrides(root / PRICES_FILE))
    requests = []
    reviews = []

    try:
        limiter = (
            asyncio.Semaphore(settings.max_concurrency)
            if settings.max_concurrency is not None
            else None
        )

        client = configured_client(
            root,
            "reviewer",
            ledger,
            output / "usage.jsonl",
            settings=settings,
            prices=prices,
            **({"limiter": limiter} if limiter is not None else {}),
        )

        agent = Reviewer(root, spec.name, client)

        for name, request in prepared:
            requests.append(request.model_dump(mode="json"))

            atomic_json(output / "requests.json", requests)

            try:
                review = await agent(request)

                reviews.append({"control": name, "review": review.model_dump(mode="json")})
            except BudgetExhausted:
                reviews.append({"control": name, "failure": "failed_budget"})
            except Exception as error:
                reviews.append(
                    {"control": name, "failure": getattr(error, "outcome", "failed_api")}
                )

            atomic_json(output / "reviews.json", reviews)

        passed = (
            len(reviews) == 2
            and reviews[0].get("review", {}).get("accepted") is True
            and reviews[1].get("review", {}).get("accepted") is False
            and "unsupported_claim" in reviews[1]["review"]["hard_fail"]
        )

        report = {
            "provenance": "live configured provider; no synthetic transport",
            "model": resolve_model(settings, load_agent(root, "reviewer")),
            "reasoning_effort_override": settings.reasoning_effort_override,
            "verdict": "PASS" if passed else "FAIL",
            "totals": ledger.totals(),
            "inputs_sha256": {
                name: hashlib.sha256(local_file(root, name).read_bytes()).hexdigest()
                for name in [
                    "agents/reviewer/system.md",
                    "agents/reviewer/agent.yaml",
                    f"post_types/{spec.name}/{spec.rubric}",
                    "inputs/kb.json",
                    "inputs/brand-voice.md",
                    "config/live-review-controls.json",
                ]
            },
        }

        atomic_json(output / "report.json", report)

        return passed
    finally:
        prices.close()


async def execute(args):
    slots = load_plan(args.root, args.root / "inputs/plan.json")

    settings = Settings(
        model_override=args.model,
        reasoning_effort_override=args.reasoning_effort,
        run_token_budget=args.token_budget,
        run_cost_budget_usd=args.cost_budget,
        allow_unpriced=args.allow_unpriced,
        max_concurrency=args.max_concurrency,
    )

    validate_inputs(args.root, slots, settings)

    if args.output.exists():
        raise ValueError("proof output already exists")

    args.output.mkdir(parents=True, exist_ok=False)

    passed = True

    if args.mode in {"reviewer", "both"}:
        passed = await reviewer_control(
            args.root,
            args.output / "grounding",
            args.model,
            min(args.token_budget, 24000),
            args.grounding_cost_budget,
            args.allow_unpriced,
            args.max_concurrency,
            reasoning_effort_override=args.reasoning_effort,
        )

    if args.mode in {"sample", "both"} and passed:
        summary = await run_pipeline(args.root, slots, args.output / "sample-week", settings)

        atomic_json(
            args.output / "sample-provenance.json",
            {
                "provenance": "live configured model/search; no injected factory",
                "model": args.model,
                "reasoning_effort_override": settings.reasoning_effort_override,
                "complete_six": len(slots) == 6 and summary.shipped == 6,
                "summary": summary.model_dump(mode="json"),
                "note": "KB owner approval remains pending.",
            },
        )

        passed = summary.shipped == len(slots) == 6

    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explicit real reviewer control and six-slot sample; paid/network calls require --live."
    )

    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-unpriced", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=["reviewer", "sample", "both"], default="both")
    parser.add_argument(
        "--model", help="Override every agent's model; omit to use each agent.yaml model."
    )
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high"])
    parser.add_argument("--token-budget", type=int, default=400000)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--cost-budget", type=float, default=1.0)
    parser.add_argument("--grounding-cost-budget", type=float, default=0.2)

    args = parser.parse_args(argv)

    if not args.live:
        print(
            "NOT_RUN: explicit --live and a new --output directory required; no clients initialized."
        )

        return 0

    if (
        args.output is None
        or not 0 < args.cost_budget <= 1
        or not 0 < args.grounding_cost_budget <= 0.25
        or args.token_budget < 1
    ):
        parser.error(
            "live requires new output, positive token budget, sample USD cap <=1 and grounding cap <=0.25"
        )

    try:
        return asyncio.run(execute(args))
    except Exception as error:
        print("live_proof_error:" + type(error).__name__)

        return 2


if __name__ == "__main__":
    raise SystemExit(main())
