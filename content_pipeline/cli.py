import argparse
import asyncio
import sys
from pathlib import Path
from .config import Settings, load_plan
from .models import ResolvedSlot
from .pipeline import estimate, parse_sets, run_pipeline
from .durability import resume_inputs


def parser(piece=False):
    cli = argparse.ArgumentParser(
        description="Produce grounded X content through the configured pipeline."
    )

    cli.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    cli.add_argument("--output", type=Path)
    cli.add_argument("--resume", type=Path)
    cli.add_argument("--no-advisor", action="store_true")

    if piece:
        cli.add_argument("--type")
        cli.add_argument("--topic")
    else:
        cli.add_argument("--plan", type=Path)
        cli.add_argument("--set", action="append", default=[])

    cli.add_argument("--estimate", action="store_true")
    cli.add_argument("--default-model")
    cli.add_argument("--model-override")
    cli.add_argument("--reasoning-effort-override", choices=["none", "low", "medium", "high"])
    cli.add_argument("--max-tweet-chars", type=int)
    cli.add_argument("--max-concurrency", type=int)
    cli.add_argument("--token-budget", type=int)
    cli.add_argument("--cost-budget", type=float)
    cli.add_argument("--allow-unpriced", action="store_true", default=None)

    return cli


def main(argv=None, *, piece=False, model_factory=None, search_factory=None, renderer=None):
    args = parser(piece).parse_args(argv)

    try:
        overrides = {
            name: value
            for name, value in {
                "default_model": args.default_model,
                "model_override": args.model_override,
                "reasoning_effort_override": args.reasoning_effort_override,
                "max_tweet_chars": args.max_tweet_chars,
                "max_concurrency": args.max_concurrency,
                "run_token_budget": args.token_budget,
                "run_cost_budget_usd": args.cost_budget,
                "allow_unpriced": args.allow_unpriced,
            }.items()
            if value is not None
        }

        if args.resume:
            forbidden = [
                args.output,
                args.default_model,
                args.model_override,
                args.reasoning_effort_override,
                args.max_tweet_chars,
                args.max_concurrency,
                args.allow_unpriced,
                args.no_advisor,
            ]

            if piece:
                forbidden.extend([args.type, args.topic])
            else:
                forbidden.extend([args.set, args.plan])

            if any(value is not None and value is not False and value != [] for value in forbidden):
                raise ValueError("resume uses saved policy and plan")

            if args.estimate:
                policy, slots, settings, _ = resume_inputs(args.resume, overrides)
        else:
            if args.no_advisor:
                overrides["advisor_enabled"] = False

            settings = Settings.load(args.root, overrides)
            policy = args.root

        if piece and not args.resume:
            if not args.type or not args.topic:
                raise ValueError("piece requires type and topic")

            if not args.topic.strip():
                raise ValueError("topic cannot be empty")

            slots = [ResolvedSlot(slot_id="piece_1", type=args.type, focus=args.topic.strip())]
        elif not args.resume:
            requested = args.plan or Path("inputs/plan.json")
            plan = requested if requested.is_absolute() else args.root / requested
            slots = parse_sets(load_plan(args.root, plan), args.set)

        def warn(startup):
            print("startup_budget_warning: " + startup.model_dump_json(), file=sys.stderr)

        def warn_resume(inputs):
            warn(estimate(*inputs[:3]))

        # A resume reads its inputs once, under the run lock; the warning waits for them.
        if args.resume and not args.estimate:
            on_resume_inputs = warn_resume
        else:
            on_resume_inputs = None
            startup = estimate(policy, slots, settings)

            if args.estimate:
                print(startup.model_dump_json(indent=2))

                return 0

            warn(startup)

        result = asyncio.run(
            run_pipeline(
                args.root,
                None if args.resume else slots,
                args.output,
                None if args.resume else settings,
                model_factory,
                search_factory,
                renderer,
                resume=args.resume,
                budget_overrides=overrides if args.resume else None,
                on_resume_inputs=on_resume_inputs,
            )
        )

        print(result.model_dump_json(indent=2))

        return 0
    except Exception as error:
        # Provider/config exceptions can embed credentials in their input/context.
        print("pipeline_error:" + type(error).__name__, file=sys.stderr)

        return 2
