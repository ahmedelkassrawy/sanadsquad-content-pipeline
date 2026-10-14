import argparse
import asyncio
import json
import sys
from pathlib import Path
from .config import Settings, load_plan
from .models import ResolvedSlot
from .pipeline import estimate, parse_sets, run_pipeline


def parser(piece=False):
    cli=argparse.ArgumentParser(description="Produce grounded X content through the configured pipeline.")
    cli.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    cli.add_argument("--output",type=Path)
    if piece:
        cli.add_argument("--type",required=True)
        cli.add_argument("--topic",required=True)
    else:
        cli.add_argument("--plan",type=Path,default=Path("inputs/plan.json"))
        cli.add_argument("--set",action="append",default=[])
    cli.add_argument("--estimate",action="store_true")
    cli.add_argument("--default-model")
    cli.add_argument("--model-override")
    cli.add_argument("--max-tweet-chars",type=int)
    cli.add_argument("--token-budget",type=int)
    cli.add_argument("--cost-budget",type=float)
    cli.add_argument("--allow-unpriced",action="store_true",default=None)
    return cli


def main(argv=None,*,piece=False,model_factory=None,search_factory=None,renderer=None):
    args=parser(piece).parse_args(argv)
    try:
        overrides={name:value for name,value in {"default_model":args.default_model,"model_override":args.model_override,"max_tweet_chars":args.max_tweet_chars,"run_token_budget":args.token_budget,"run_cost_budget_usd":args.cost_budget,"allow_unpriced":args.allow_unpriced}.items() if value is not None}
        settings=Settings.load(args.root,overrides)
        if piece:
            if not args.topic.strip(): raise ValueError("topic cannot be empty")
            slots=[ResolvedSlot(slot_id="piece_1",type=args.type,focus=args.topic.strip())]
        else:
            plan=args.plan if args.plan.is_absolute() else args.root/args.plan
            slots=parse_sets(load_plan(args.root,plan),args.set)
        if args.estimate:
            print(estimate(args.root,slots,settings).model_dump_json(indent=2))
            return 0
        result=asyncio.run(run_pipeline(args.root,slots,args.output,settings,model_factory,search_factory,renderer))
        print(result.model_dump_json(indent=2))
        return 0
    except Exception as error:
        # Provider/config exceptions can embed credentials in their input/context.
        print("pipeline_error:"+type(error).__name__,file=sys.stderr)
        return 2
