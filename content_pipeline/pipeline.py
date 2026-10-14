import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4
import yaml
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from .agents import ResearchAgent, TopicSelector, Writer, Reviewer, VisualAgent, load_rubric
from .config import Settings, load_agent, load_brand, load_type, local_file, resolve_model
from .contracts import DraftAudit, DraftRecord, Estimate, ExportPost, ExternalUsage, FinalPiece, ResearchRecord, ResearchRequest, ResolvedPlan, ReviewRecord, ReviewRequest, RunSummary, SlotState, TerminalSlot, TopicChoiceRequest, VisualRecord, VisualRequest, WeekSnapshot, WriteRequest
from .images import fallback_spec, overlay_errors, render_card
from .ledger import BudgetExhausted, Ledger
from .model_client import ModelFailure, Prices, configured_client
from .models import ImageResult, ResolvedSlot, SlotOutcome
from .research import KnowledgeBase, assign_topics
from .tools import configured_web_search
from .trail import Trail, export_posts
from .validation import validate


def safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",name) or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])",name):
        raise ValueError("unsafe slot/type/agent name")
    return name


def parse_sets(slots,values):
    indexed={slot.slot_id:slot.model_copy(deep=True) for slot in slots}
    for value in values:
        key,separator,focus=value.partition("=")
        if not separator or key not in indexed or not focus.strip():
            raise ValueError("--set must name an existing slot and nonempty focus")
        indexed[key].focus=focus.strip()
        indexed[key].topic_id=None
    return list(indexed.values())


def validate_inputs(root,slots,settings=None):
    if not slots or len({slot.slot_id for slot in slots})!=len(slots):
        raise ValueError("plan needs unique slots")
    from PIL import ImageColor
    rules,_=load_brand(root/"inputs/brand-voice.md")
    if len(rules.palette)<3: raise ValueError("brand palette needs three colors")
    for color in rules.palette: ImageColor.getrgb(color)
    KnowledgeBase(root/"inputs/kb.json")
    for slot in slots:
        safe_name(slot.slot_id)
        safe_name(slot.type)
        spec=load_type(root,slot.type)
        if "unsupported_claim" not in spec.review.hard_fail:
            raise ValueError("rubric must hard-fail unsupported claims")
        safe_name(spec.writer)
        load_rubric(root,spec)
        for name in [spec.writer,"research","reviewer","visual"]:
            agent=load_agent(root,name)
            if settings:
                model=resolve_model(settings,agent)
                provider,separator,reference=model.partition(":")
                if not separator or provider not in {"openrouter","opencode","openai","google_genai","anthropic"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*",reference) or (provider=="openrouter" and "/" not in reference):
                    raise ValueError("invalid configured model reference")
    for name in ["agents/research/loop.md","agents/research/topics.md","config/repair.md"]:
        local_file(root,name)


def estimate(root,slots,settings):
    validate_inputs(root,slots,settings)
    tokens=0
    cost=0.0
    unpriced=False
    warnings=[]
    prices=Prices(update=False)
    names=["research"]+ [name for slot in slots for name in ["research",load_type(root,slot.type).writer,"reviewer","visual"]]
    for name in names:
        config=load_agent(root,name)
        # Includes up to 4 search turns, two content attempts, parsing/truncation
        # allowances and three transient retries. Input is still an estimate.
        calls=5 if name=="research" else 2 if name in {load_type(root,s.type).writer for s in slots}|{"reviewer"} else 1
        output=int(config.max_output_tokens*1.5)
        reserved=(16000+output)*calls*8
        tokens+=reserved
        try:
            quoted,_=prices.quote(resolve_model(settings,config),16000,output)
            cost+=quoted*calls*8
        except LookupError:
            unpriced=True
            warnings.append("unpriced_model:"+name)
    if unpriced: cost=None
    if tokens>settings.run_token_budget or (cost is not None and cost>settings.run_cost_budget_usd):
        warnings.append("worst_case_exceeds_budget")
    return Estimate(slots=len(slots),worst_case_tokens=tokens,worst_case_cost_usd=cost,token_budget=settings.run_token_budget,cost_budget_usd=settings.run_cost_budget_usd,warnings=warnings)


def history(parent):
    runs=sorted((path for path in parent.iterdir() if path.is_dir() and (path/"run_summary.json").is_file()),key=lambda p:p.stat().st_mtime)
    result=[]
    for path in runs[-4:]:
        try:
            summary=RunSummary.model_validate_json((path/"run_summary.json").read_text(encoding="utf-8"))
            result.append([slot.topic_id for slot in summary.slots.values() if slot.topic_id])
        except (ValueError,OSError):
            continue
    return result


def snapshot(root,run_dir,slots,settings):
    target=run_dir/"snapshot"
    target.mkdir()
    for name in ["inputs/brand-voice.md","inputs/kb.json"]:
        source=local_file(root,name)
        destination=target/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
    files={"agents/research/loop.md","agents/research/topics.md","config/repair.md","config/visual.yaml"}
    names={"research","reviewer","visual"}
    for slot in slots:
        spec=load_type(root,slot.type)
        names.add(spec.writer)
        for name in ["type.yaml","writer.md",spec.rubric,spec.research.hints,spec.research.angles]:
            if name: files.add(f"post_types/{slot.type}/{name}")
    for name in names:
        files.update([f"agents/{name}/agent.yaml",f"agents/{name}/system.md"])
        for skill in load_agent(root,name).skills:
            files.update([f"skills/{skill}.md",f"skills/{skill}.py"])
    for name in sorted(files):
        source=local_file(root,name)
        destination=target/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
    (target/"inputs/plan.json").write_text(json.dumps({"slots":[slot.model_dump(mode="json") for slot in slots]},indent=2),encoding="utf-8")
    (target/"resolved-config.json").write_text(settings.model_dump_json(indent=2),encoding="utf-8")
    return target


@dataclass
class RunContext:
    root: Path
    credential_root: Path
    run_dir: Path
    settings: Settings
    ledger: Ledger
    prices: Prices
    usage_lock: asyncio.Lock


def default_model_factory(name,slot_id,context):
    return configured_client(context.root,name,context.ledger,context.run_dir/"usage.jsonl",settings=context.settings,credential_root=context.credential_root,prices=context.prices,usage_lock=context.usage_lock)


def merge_slots(left,right):
    return {**left,**right}


class WeekState(TypedDict):
    slots: Annotated[dict,merge_slots]
    warnings: list[str]
    summary: dict


class LaneState(TypedDict):
    slot: dict
    attempt: int
    pack: dict | None
    draft: dict | None
    audit: dict | None
    review: dict | None
    image: dict | None
    outcome: str | None
    reasons: list[str]
    feedback: list[str]
    slots: dict


def lane_state(raw):
    return SlotState.model_validate({key:value for key,value in raw.items() if key in SlotState.model_fields})


def failure(error,research=False):
    if isinstance(error,BudgetExhausted): return SlotOutcome.failed_budget
    if isinstance(error,ModelFailure):
        if error.outcome in {"failed_budget","unpriced_model"}: return SlotOutcome.failed_budget
        if research and error.outcome in {"failed_output","failed_research"}: return SlotOutcome.failed_research
        if error.outcome in SlotOutcome._value2member_map_: return SlotOutcome(error.outcome)
    return SlotOutcome.failed_research if research else SlotOutcome.failed_api


class Pipeline:
    def __init__(self,context,slots,model_factory=default_model_factory,search_factory=configured_web_search,renderer=render_card):
        self.context=context
        self.root=context.root
        self.plan=slots
        self.rules,_=load_brand(self.root/"inputs/brand-voice.md")
        self.kb=KnowledgeBase(self.root/"inputs/kb.json")
        self.model_factory=model_factory
        self.search_factory=search_factory
        self.renderer=renderer
        self.searchers=[]
        self.external=ExternalUsage()
        self.trails={name:Trail(context.run_dir/(name+".json")) for name in ["research_log","drafts","review_report","visuals","final"]}
        for trail in self.trails.values(): trail.path.write_text("[]",encoding="utf-8")
        self.writers={slot.slot_id:Writer(self.root,slot.type,self.client(load_type(self.root,slot.type).writer,slot.slot_id)) for slot in slots}
        self.reviewers={slot.slot_id:Reviewer(self.root,slot.type,self.client("reviewer",slot.slot_id)) for slot in slots}
        self.visuals={slot.slot_id:VisualAgent(self.root,self.client("visual",slot.slot_id)) for slot in slots}

    def client(self,name,slot_id): return self.model_factory(name,slot_id,self.context)

    async def record(self,name,record):
        await self.trails[name].append(record.record_id,record.model_dump(mode="json",exclude={"record_id"}))

    async def research_slot(self,state):
        slot=state.slot
        agent=None
        try:
            searcher=self.search_factory(self.context.credential_root)
            self.searchers.append(searcher)
            agent=ResearchAgent(self.root,self.client("research",slot.slot_id),self.kb,searcher)
            spec=load_type(self.root,slot.type)
            hints=local_file(self.root/"post_types"/slot.type,spec.research.hints).read_text(encoding="utf-8")
            pack=await agent(ResearchRequest(slot=slot,hints=hints))
            if pack.slot_id!=slot.slot_id: raise ModelFailure("failed_output")
            state.pack=pack
            state.slot.topic_id=pack.topic_id
        except Exception as error:
            state.outcome=failure(error,research=True)
            state.reasons=[state.outcome.value]
        for record in agent.records if agent else []:
            await self.record("research_log",record)
        await self.record("research_log",ResearchRecord(record_id=f"{slot.slot_id}/research/1/1",slot_id=slot.slot_id,attempt=1,pack=state.pack,evidence=state.pack.sources if state.pack else [],action="resolved" if state.pack else "failed",failure=state.outcome.value if state.outcome else None))
        return state.model_dump(mode="json")

    async def focused(self,week):
        states=[SlotState.model_validate(value) for value in week["slots"].values()]
        focused=[state for state in states if state.slot.focus]
        results=await asyncio.gather(*(self.research_slot(state) for state in focused))
        return {"slots":{state["slot"]["slot_id"]:state for state in results}}

    async def select(self,week):
        states=[SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        empty=[state for state in states if not state.slot.focus]
        warnings=[]
        proposals={}
        prior=history(self.context.run_dir.parent)
        if empty:
            choices={}
            for state in empty:
                spec=load_type(self.root,state.slot.type)
                if spec.research.topic_source=="kb": choices[state.slot.slot_id]=[entry["id"] for entry in self.kb.entries]
                else: choices[state.slot.slot_id]=[angle["id"] for angle in yaml.safe_load(local_file(self.root/"post_types"/spec.name,spec.research.angles).read_text(encoding="utf-8"))["angles"]]
            excluded=[state.slot.topic_id for state in states if state.slot.topic_id]+[id for run in prior for id in run]
            try:
                proposals=(await TopicSelector(self.root,self.client("research","_batch"))(TopicChoiceRequest(choices=choices,exclude=excluded))).assignments
            except Exception:
                warnings.append("topic_selection_failed:code_fallback")
        try:
            assigned,notes=assign_topics(self.root,[state.slot for state in states],proposals,self.kb,prior)
        except ValueError:
            assigned={}
            notes=[]
            selected=[state.slot for state in states if state.slot.focus]
            for state in empty:
                try:
                    partial,new_notes=assign_topics(self.root,[*selected,state.slot],{**proposals,**assigned},self.kb,prior)
                    assigned[state.slot.slot_id]=partial[state.slot.slot_id]
                    selected.append(state.slot)
                    notes.extend(new_notes)
                except ValueError:
                    state.outcome=SlotOutcome.failed_research
                    state.reasons=["topic_pool_exhausted"]
        warnings=list(dict.fromkeys(warnings+notes))
        for state in empty:
            if not state.outcome: state.slot.topic_id=assigned[state.slot.slot_id]
            else:
                await self.record("research_log",ResearchRecord(record_id=f"{state.slot.slot_id}/research/1/1",slot_id=state.slot.slot_id,attempt=1,evidence=[],action="failed",failure="topic_pool_exhausted"))
        results=await asyncio.gather(*(self.research_slot(state) for state in empty if not state.outcome))
        results.extend(state.model_dump(mode="json") for state in empty if state.outcome)
        changed={state["slot"]["slot_id"]:state for state in results}
        resolved=[ResolvedSlot.model_validate((changed.get(state.slot.slot_id) or state.model_dump(mode="json"))["slot"]) for state in states]
        record=ResolvedPlan(record_id="_run/plan/1/1",slot_id="_run",attempt=1,slots=resolved,warnings=warnings)
        (self.context.run_dir/"plan.resolved.json").write_text(record.model_dump_json(indent=2),encoding="utf-8")
        return {"slots":changed,"warnings":warnings}

    async def write(self,raw):
        state=lane_state(raw)
        state.attempt+=1
        state.draft=None
        state.audit=None
        state.review=None
        request=WriteRequest(slot_id=state.slot.slot_id,attempt=state.attempt,research=state.pack,post_type=load_type(self.root,state.slot.type),feedback=state.feedback)
        try:
            state.draft=await self.writers[state.slot.slot_id](request)
        except Exception as error:
            outcome=failure(error)
            if outcome==SlotOutcome.failed_output:
                state.feedback=["writer:failed_output"]
            else:
                state.outcome=outcome
                state.reasons=["unpriced_model" if isinstance(error,ModelFailure) and error.outcome=="unpriced_model" else outcome.value]
            await self.record("drafts",DraftRecord(record_id=f"{state.slot.slot_id}/writer/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,failure=outcome.value))
        return state.model_dump(mode="json")

    async def validation(self,raw):
        state=lane_state(raw)
        result=validate(state.draft,state.pack,load_type(self.root,state.slot.type),self.rules,self.context.settings.max_tweet_chars)
        state.audit=DraftAudit(original=result.original,normalized=[tweet.model_dump() for tweet in result.normalized.tweets],fixes=result.fixes,violations=result.violations,accepted=result.accepted)
        state.feedback=result.violations
        await self.record("drafts",DraftRecord(record_id=f"{state.slot.slot_id}/validator/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,audit=state.audit))
        return state.model_dump(mode="json")

    async def review(self,raw):
        state=lane_state(raw)
        spec=load_type(self.root,state.slot.type)
        try:
            state.review=await self.reviewers[state.slot.slot_id](ReviewRequest(slot_id=state.slot.slot_id,attempt=state.attempt,draft=state.audit.approved(),research=state.pack,post_type=spec,rubric=load_rubric(self.root,spec)))
            state.feedback=state.review.reasons
        except Exception as error:
            state.outcome=failure(error)
            state.reasons=[state.outcome.value]
        await self.record("review_report",ReviewRecord(record_id=f"{state.slot.slot_id}/reviewer/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,review=state.review,failure=state.outcome.value if state.outcome else None))
        return state.model_dump(mode="json")

    async def retry(self,raw):
        state=lane_state(raw)
        if state.attempt>=2:
            state.outcome=SlotOutcome.dropped_quality
            state.reasons=state.feedback or ["quality_rejected"]
        return state.model_dump(mode="json")

    async def visual(self,raw):
        state=lane_state(raw)
        severity=next((entry["severity"] for entry in self.kb.entries if entry["id"]==state.pack.topic_id),None)
        request=VisualRequest(slot_id=state.slot.slot_id,attempt=state.attempt,draft=state.audit.approved(),research=state.pack,post_type=load_type(self.root,state.slot.type),severity=severity)
        reason=None
        try:
            spec=await self.visuals[state.slot.slot_id](request)
            if overlay_errors(spec,request,self.rules): raise ValueError("invalid_overlay")
        except Exception:
            reason="visual_fallback"
            spec=fallback_spec(request,self.rules)
        if overlay_errors(spec,request,self.rules):
            raise ValueError("fallback overlay failed grounding")
        filename="slot-"+safe_name(state.slot.slot_id)
        spec_path=self.context.run_dir/"images"/(filename+".json")
        spec_path.parent.mkdir(exist_ok=True)
        spec_path.write_text(spec.model_dump_json(indent=2),encoding="utf-8")
        path=spec_path.with_suffix(".png")
        self.external.image_calls+=1
        try:
            await asyncio.to_thread(self.renderer,spec,path)
            from PIL import Image
            with Image.open(path) as image:
                if image.format!="PNG" or image.size!=(1200,675): raise ValueError("invalid rendered card")
                image.verify()
            self.external.pillow_cards+=1
            backend="pillow_card"
        except Exception:
            path=spec_path
            backend="spec_json"
            reason="pillow_fallback_spec"
        state.image=ImageResult(slot_id=state.slot.slot_id,path=path.relative_to(self.context.run_dir).as_posix(),backend=backend,fallback_reason=reason)
        state.outcome=SlotOutcome.shipped
        await self.record("visuals",VisualRecord(record_id=f"{state.slot.slot_id}/visual/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,spec=spec,result=state.image))
        return state.model_dump(mode="json")

    async def collect(self,raw):
        state=lane_state(raw)
        if state.outcome is None: raise ValueError("nonterminal slot at join")
        if state.outcome==SlotOutcome.shipped:
            piece=FinalPiece(record_id=f"{state.slot.slot_id}/final/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,topic_id=state.pack.topic_id,topic_name=state.pack.topic_name,draft=state.audit.approved(),image=state.image)
            await self.record("final",piece)
        return {"slots":{state.slot.slot_id:state.model_dump(mode="json")}}

    async def finalize(self,week):
        states=[SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        accepted=[]
        for state in states:
            if state.outcome==SlotOutcome.shipped:
                accepted.append(ExportPost(record_id=f"{state.slot.slot_id}/export/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,type=state.slot.type,topic_id=state.pack.topic_id,topic_name=state.pack.topic_name,tweets=[tweet.text for tweet in state.audit.approved().tweets],image=state.image.path).model_dump(mode="json"))
        export_posts(self.context.run_dir,accepted)
        totals=self.context.ledger.totals()
        self.external.search_calls=sum(searcher.calls for searcher in self.searchers)
        self.external.tavily_credits=sum(searcher.tavily_credits for searcher in self.searchers)
        for key in ["tavily_calls","ddgs_calls","fetch_calls"]:
            setattr(self.external,key,sum(getattr(searcher,key,0) for searcher in self.searchers))
        self.external.tavily_credits_estimated=any(getattr(searcher,"tavily_credits_estimated",False) for searcher in self.searchers)
        usage=[json.loads(line) for line in (self.context.run_dir/"usage.jsonl").read_text(encoding="utf-8").splitlines()]
        summary=RunSummary(record_id="_run/summary/1/1",slot_id="_run",attempt=1,run_id=self.context.run_dir.name,slots={state.slot.slot_id:TerminalSlot(outcome=state.outcome,attempts=state.attempt,topic_id=state.slot.topic_id,reasons=state.reasons) for state in states},shipped=len(accepted),confirmed_tokens=totals["tokens"]-totals["uncertain_tokens"],uncertain_tokens=totals["uncertain_tokens"],confirmed_cost_usd=totals["cost_usd"]-totals["uncertain_cost_usd"],uncertain_cost_usd=totals["uncertain_cost_usd"],unpriced=any(record["cost_source"]=="unknown" for record in usage) or any("unpriced_model" in state.reasons for state in states),external_usage=self.external)
        (self.context.run_dir/"run_summary.json").write_text(summary.model_dump_json(indent=2),encoding="utf-8")
        (self.context.run_dir/"run.log").write_text("export: complete\nsummary: complete\n",encoding="utf-8")
        return {"summary":summary.model_dump(mode="json")}

    def graph(self,checkpointer):
        lane=StateGraph(LaneState)
        for name,node in [("write",self.write),("validate",self.validation),("review",self.review),("retry",self.retry),("visual",self.visual),("collect",self.collect)]: lane.add_node(name,node)
        lane.add_conditional_edges(START,lambda state:"collect" if state.get("outcome") else "write")
        lane.add_conditional_edges("write",lambda state:"collect" if state.get("outcome") else "validate" if state.get("draft") else "retry")
        lane.add_conditional_edges("validate",lambda state:"review" if state["audit"]["accepted"] else "retry")
        lane.add_conditional_edges("review",lambda state:"collect" if state.get("outcome") else "visual" if state["review"]["accepted"] else "retry")
        lane.add_conditional_edges("retry",lambda state:"collect" if state.get("outcome") else "write")
        lane.add_edge("visual","collect")
        lane.add_edge("collect",END)
        graph=StateGraph(WeekState)
        graph.add_node("focused_research",self.focused)
        graph.add_node("topic_selection",self.select)
        graph.add_node("lane",lane.compile())
        graph.add_node("export_summary",self.finalize)
        graph.add_edge(START,"focused_research")
        graph.add_edge("focused_research","topic_selection")
        graph.add_conditional_edges("topic_selection",lambda state:[Send("lane",state["slots"][slot.slot_id]) for slot in self.plan])
        graph.add_edge("lane","export_summary")
        graph.add_edge("export_summary",END)
        return graph.compile(checkpointer=checkpointer)


async def run_pipeline(root: Path,slots: list[ResolvedSlot],output: Path | None=None,settings: Settings | None=None,model_factory=None,search_factory=None,renderer=None) -> RunSummary:
    root=root.resolve()
    settings=settings or Settings.load(root)
    validate_inputs(root,slots,settings)
    run_dir=(output or root/"output"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid4().hex[:8])).resolve()
    if run_dir.exists(): raise ValueError("output run already exists; choose a new directory")
    run_dir.mkdir(parents=True,exist_ok=False)
    snapshot_root=snapshot(root,run_dir,slots,settings)
    (run_dir/"usage.jsonl").write_text("",encoding="utf-8")
    ledger=Ledger(run_dir/"ledger.json",settings.run_token_budget,settings.run_cost_budget_usd)
    prices=Prices(update=model_factory is None)
    context=RunContext(snapshot_root,root,run_dir,settings,ledger,prices,asyncio.Lock())
    try:
        pipeline=Pipeline(context,slots,model_factory or default_model_factory,search_factory or configured_web_search,renderer or render_card)
        async with AsyncSqliteSaver.from_conn_string(str(run_dir/"checkpoint.sqlite")) as saver:
            graph=pipeline.graph(saver)
            config={"configurable":{"thread_id":run_dir.name},"recursion_limit":50}
            result=await graph.ainvoke({"slots":{slot.slot_id:SlotState(slot=slot).model_dump(mode="json") for slot in slots},"warnings":[]},config)
            saved=await graph.aget_state(config)
            (run_dir/"state.json").write_text(WeekSnapshot.model_validate(saved.values).model_dump_json(indent=2),encoding="utf-8")
        return RunSummary.model_validate(result["summary"])
    finally:
        prices.close()
