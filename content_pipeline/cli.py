import argparse
import asyncio
import json
import sys
from pathlib import Path
from .config import Settings, load_plan
from .models import ResolvedSlot
from .pipeline import estimate, parse_sets, run_pipeline
from .durability import resume_inputs


def parser(piece=False):
    cli=argparse.ArgumentParser(description="Produce grounded X content through the configured pipeline.")
    cli.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    cli.add_argument("--output",type=Path)
    cli.add_argument("--resume",type=Path)
    cli.add_argument("--no-advisor",action="store_true")
    if piece:
        cli.add_argument("--type")
        cli.add_argument("--topic")
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
        if args.resume:
            forbidden=[args.output,args.default_model,args.model_override,args.max_tweet_chars,args.allow_unpriced,args.no_advisor]
            if piece: forbidden.extend([args.type,args.topic])
            else: forbidden.extend([args.set,args.plan!=Path("inputs/plan.json")])
            if any(value is not None and value is not False and value!=[] for value in forbidden):
                raise ValueError("resume uses saved policy and plan")
            policy,slots,settings,_=resume_inputs(args.resume,overrides)
        else:
            if args.no_advisor: overrides["advisor_enabled"]=False
            settings=Settings.load(args.root,overrides)
            policy=args.root
        if piece and not args.resume:
            if not args.type or not args.topic: raise ValueError("piece requires type and topic")
            if not args.topic.strip(): raise ValueError("topic cannot be empty")
            slots=[ResolvedSlot(slot_id="piece_1",type=args.type,focus=args.topic.strip())]
        elif not args.resume:
            plan=args.plan if args.plan.is_absolute() else args.root/args.plan
            slots=parse_sets(load_plan(args.root,plan),args.set)
        startup=estimate(policy,slots,settings)
        if args.estimate:
            print(startup.model_dump_json(indent=2))
            return 0
        print("startup_budget_warning: "+startup.model_dump_json(),file=sys.stderr)
        result=asyncio.run(run_pipeline(args.root,None if args.resume else slots,args.output,None if args.resume else settings,model_factory,search_factory,renderer,resume=args.resume,budget_overrides=overrides if args.resume else None))
        print(result.model_dump_json(indent=2))
        return 0
    except Exception as error:
        # Provider/config exceptions can embed credentials in their input/context.
        print("pipeline_error:"+type(error).__name__,file=sys.stderr)
        return 2
