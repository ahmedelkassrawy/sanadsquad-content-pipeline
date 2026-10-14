import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from .agents import ResearchAgent, TopicSelector, Writer, Reviewer, VisualAgent, load_rubric
from .config import Settings, load_agent, load_angles, load_brand, load_type, local_file, resolve_model
from .contracts import DraftAudit, DraftRecord, Estimate, ExportPost, ExternalUsage, FinalPiece, ResearchRecord, ResearchRequest, ResolvedPlan, ReviewRecord, ReviewRequest, RunSummary, SlotState, TerminalSlot, TopicChoiceRequest, VisualRecord, VisualRequest, WeekSnapshot, WriteRequest
from .images import fallback_spec, overlay_errors, render_card
from .ledger import BudgetExhausted, Ledger
from .model_client import ModelFailure, Prices, configured_client
from .models import ImageResult, ResolvedSlot, SlotOutcome
from .research import KnowledgeBase, assign_topics
from .tools import configured_web_search
from .trail import Trail, export_posts
from .validation import validate
from .advisor import Advisor, advice_patch, instruction_files
from .contracts import AdvisorRecord, AdvisorRequest, RunIdentity
from .durability import RunLock, atomic_json, resume_inputs, snapshot_hashes


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
    if not slots or len({slot.slot_id.casefold() for slot in slots})!=len(slots):
        raise ValueError("plan needs case-insensitive unique slots")
    from PIL import ImageColor
    rules,_=load_brand(root/"inputs/brand-voice.md")
    if len(rules.palette)<3: raise ValueError("brand palette needs three colors")
    for color in rules.palette: ImageColor.getrgb(color)
    KnowledgeBase(root/"inputs/kb.json")
    for slot in slots:
        safe_name(slot.slot_id)
        safe_name(slot.type)
        spec=load_type(root,slot.type)
        if spec.research.topic_source=="angles": load_angles(root,spec)
        if "unsupported_claim" not in spec.review.hard_fail:
            raise ValueError("rubric must hard-fail unsupported claims")
        safe_name(spec.writer)
        load_rubric(root,spec)
        for name in [spec.writer,"research","reviewer","visual"]+(["advisor"] if not settings or settings.advisor_enabled else []):
            agent=load_agent(root,name)
            local_file(root/"agents",name+"/system.md")
            for skill in agent.skills:
                local_file(root/"skills",skill+".md")
                local_file(root/"skills",skill+".py")
            if settings:
                model=resolve_model(settings,agent)
                provider,separator,reference=model.partition(":")
                if not separator or provider not in {"openrouter","opencode","openai","google_genai","anthropic"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*",reference) or (provider=="openrouter" and "/" not in reference):
                    raise ValueError("invalid configured model reference")
    for name in ["agents/research/loop.md","agents/research/topics.md","config/repair.md","config/visual.yaml"]:
        local_file(root,name)


def estimate(root,slots,settings):
    validate_inputs(root,slots,settings)
    tokens=0
    cost=0.0
    unpriced=False
    warnings=[]
    prices=Prices(update=False)
    names=["research"]+ [name for slot in slots for name in ["research",load_type(root,slot.type).writer,"reviewer","visual"]]+(["advisor"] if settings.advisor_enabled else [])
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
    names={"research","reviewer","visual","advisor"}
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
    identity: RunIdentity | None = None


def default_model_factory(name,slot_id,context):
    return configured_client(context.root,name,context.ledger,context.run_dir/"usage.jsonl",settings=context.settings,credential_root=context.credential_root,prices=context.prices,usage_lock=context.usage_lock)


def merge_slots(left,right):
    return {**left,**right}


class WeekState(TypedDict):
    slots: Annotated[dict,merge_slots]
    warnings: list[str]
    summary: dict
    advisor: str
    advisor_reason: str | None


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
        external=context.run_dir/"external-usage.json"
        self.external=ExternalUsage.model_validate_json(external.read_text(encoding="utf-8")) if external.exists() else ExternalUsage()
        self.external_lock=asyncio.Lock()
        self.search_seen={}
        self.research_replay_lock=asyncio.Lock()
        self.trails={name:Trail(context.run_dir/(name+".json")) for name in ["research_log","drafts","review_report","visuals","final","advisor"]}
        for trail in self.trails.values():
            if not trail.path.exists(): trail.path.write_text("[]",encoding="utf-8")
        self.writers={}
        self.reviewers={}
        self.visuals={}

    def client(self,name,slot_id): return self.model_factory(name,slot_id,self.context)

    async def record(self,name,record):
        await self.trails[name].append(record.record_id,record.model_dump(mode="json",exclude={"record_id"}))

    def saved_record(self,name,record_id,model,request):
        raw=next((record for record in json.loads(self.trails[name].path.read_text(encoding="utf-8")) if record["record_id"]==record_id),None)
        if raw is None: return None
        record=model.model_validate(raw)
        if record.input_sha256!=hashlib.sha256(request.model_dump_json().encode()).hexdigest():
            raise ValueError("saved stage input mismatch")
        return record

    async def save_search_usage(self,searcher):
        mapping={"search_calls":"calls","tavily_calls":"tavily_calls","ddgs_calls":"ddgs_calls","fetch_calls":"fetch_calls","tavily_credits":"tavily_credits"}
        async with self.external_lock:
            previous=self.search_seen.setdefault(id(searcher),{})
            for key,attribute in mapping.items():
                value=getattr(searcher,attribute,0)
                setattr(self.external,key,getattr(self.external,key)+value-previous.get(key,0))
                previous[key]=value
            self.external.tavily_credits_estimated|=getattr(searcher,"tavily_credits_estimated",False)
            atomic_json(self.context.run_dir/"external-usage.json",self.external.model_dump())

    async def image_count(self,key):
        async with self.external_lock:
            setattr(self.external,key,getattr(self.external,key)+1)
            atomic_json(self.context.run_dir/"external-usage.json",self.external.model_dump())

    @staticmethod
    def replay_digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")).hexdigest()

    def research_request_digest(self,request):
        value=request.model_dump(mode="json")
        if request.slot.focus:
            # Focused research writes its resolved topic back to the slot; compare with the snapshot input.
            planned=next(slot for slot in self.plan if slot.slot_id==request.slot.slot_id)
            value["slot"]["topic_id"]=planned.topic_id
        return self.replay_digest(value)

    async def saved_research(self,record_id,request):
        path=self.trails["research_log"].path
        raw=next((item for item in json.loads(path.read_text(encoding="utf-8")) if item["record_id"]==record_id),None)
        if raw is None:
            return None
        record=ResearchRecord.model_validate(raw)
        if record.slot_id!=request.slot.slot_id or record.attempt!=request.attempt:
            raise ModelFailure("failed_research")
        if record.pack is None:
            return record
        receipt_path=self.context.run_dir/"research-replay.json"
        receipts=json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        receipt=receipts.get(record_id)
        expected_request=self.research_request_digest(request)
        expected_snapshot=self.replay_digest(self.context.identity.snapshot_files)
        expected_pack=self.replay_digest(record.pack.model_dump(mode="json"))
        if (record.action!="resolved" or record.failure is not None or record.pack.slot_id!=request.slot.slot_id
                or record.evidence!=record.pack.sources or not receipt
                or receipt!={"request_sha256":expected_request,"snapshot_sha256":expected_snapshot,"pack_sha256":expected_pack}):
            raise ModelFailure("failed_research")
        return record

    async def save_research_receipt(self,record_id,request,pack):
        receipt={"request_sha256":self.research_request_digest(request),
                 "snapshot_sha256":self.replay_digest(self.context.identity.snapshot_files),
                 "pack_sha256":self.replay_digest(pack.model_dump(mode="json"))}
        async with self.research_replay_lock:
            path=self.context.run_dir/"research-replay.json"
            receipts=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            receipts[record_id]=receipt
            atomic_json(path,receipts)

    async def research_slot(self,state):
        slot=state.slot
        agent=None
        try:
            spec=load_type(self.root,slot.type)
            hints=local_file(self.root/"post_types"/slot.type,spec.research.hints).read_text(encoding="utf-8")
            request=ResearchRequest(slot=slot,hints=hints)
            record_id=f"{slot.slot_id}/research/1/1"
            saved=await self.saved_research(record_id,request)
            if saved is not None:
                if saved.pack is None:
                    state.outcome=failure(ModelFailure(saved.failure or "failed_research"),research=True)
                    state.reasons=[state.outcome.value]
                else:
                    state.pack=saved.pack
                    state.slot.topic_id=saved.pack.topic_id
                return state.model_dump(mode="json")
            searcher=self.search_factory(self.context.credential_root)
            self.searchers.append(searcher)
            agent=ResearchAgent(self.root,self.client("research",slot.slot_id),self.kb,searcher,self.save_search_usage)
            pack=await agent(request)
            if pack.slot_id!=slot.slot_id: raise ModelFailure("failed_output")
            state.pack=pack
            state.slot.topic_id=pack.topic_id
        except Exception as error:
            state.outcome=failure(error,research=True)
            state.reasons=[state.outcome.value]
        for record in agent.records if agent else []:
            await self.record("research_log",record)
        record_id=f"{slot.slot_id}/research/1/1"
        await self.record("research_log",ResearchRecord(record_id=record_id,slot_id=slot.slot_id,attempt=1,pack=state.pack,evidence=state.pack.sources if state.pack else [],action="resolved" if state.pack else "failed",failure=state.outcome.value if state.outcome else None))
        if state.pack is not None:
            await self.save_research_receipt(record_id,request,state.pack)
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
                else: choices[state.slot.slot_id]=[angle.id for angle in load_angles(self.root,spec)]
            excluded=[state.slot.topic_id for state in states if state.slot.topic_id]+[id for run in prior for id in run]
            try:
                proposals=(await TopicSelector(self.root,self.client("research","_batch"))(TopicChoiceRequest(choices=choices,exclude=excluded))).assignments
            except Exception:
                warnings.append("topic_selection_failed:code_fallback")
        assigned,notes=assign_topics(self.root,[state.slot for state in states],proposals,self.kb,prior,allow_partial=True)
        for state in empty:
            if state.slot.slot_id not in assigned:
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
        previous_draft=state.draft
        state.draft=None
        state.audit=None
        state.review=None
        request=WriteRequest(slot_id=state.slot.slot_id,attempt=state.attempt,research=state.pack,post_type=load_type(self.root,state.slot.type),feedback=state.feedback,max_tweet_chars=self.context.settings.max_tweet_chars,previous_draft=previous_draft)
        try:
            if state.slot.slot_id not in self.writers:
                self.writers[state.slot.slot_id]=Writer(self.root,state.slot.type,self.client(load_type(self.root,state.slot.type).writer,state.slot.slot_id))
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
        request=ReviewRequest(slot_id=state.slot.slot_id,attempt=state.attempt,draft=state.audit.approved(),research=state.pack,post_type=spec,rubric=load_rubric(self.root,spec))
        record_id=f"{state.slot.slot_id}/reviewer/{state.attempt}/1"
        try:
            saved=self.saved_record("review_report",record_id,ReviewRecord,request)
            if saved:
                state.review=saved.review
                if saved.failure:
                    state.outcome=SlotOutcome(saved.failure)
                    state.reasons=[saved.failure]
                state.feedback=state.review.reasons if state.review else []
                return state.model_dump(mode="json")
            if state.slot.slot_id not in self.reviewers:
                self.reviewers[state.slot.slot_id]=Reviewer(self.root,state.slot.type,self.client("reviewer",state.slot.slot_id))
            state.review=await self.reviewers[state.slot.slot_id](request)
            state.feedback=state.review.reasons
        except Exception as error:
            state.outcome=failure(error)
            state.reasons=[state.outcome.value]
        await self.record("review_report",ReviewRecord(record_id=record_id,slot_id=state.slot.slot_id,attempt=state.attempt,review=state.review,failure=state.outcome.value if state.outcome else None,input_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest()))
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
        record_id=f"{state.slot.slot_id}/visual/{state.attempt}/1"
        saved=self.saved_record("visuals",record_id,VisualRecord,request)
        if saved:
            from pathlib import PureWindowsPath
            relative=PureWindowsPath(saved.result.path)
            if relative.anchor or ".." in relative.parts: raise ValueError("invalid saved image path")
            path=local_file(self.context.run_dir,Path(*relative.parts).as_posix())
            stored=local_file(self.context.run_dir,path.with_suffix(".json").relative_to(self.context.run_dir).as_posix())
            if json.loads(stored.read_text(encoding="utf-8"))!=saved.spec.model_dump(mode="json") or overlay_errors(saved.spec,request,self.rules):
                raise ValueError("saved visual artifact mismatch")
            if saved.result.slot_id!=state.slot.slot_id: raise ValueError("saved visual identity mismatch")
            if saved.result.backend=="pillow_card":
                from PIL import Image
                with Image.open(path) as image:
                    if image.format!="PNG" or image.size!=(1200,675): raise ValueError("invalid saved card")
                    image.verify()
            state.image=saved.result
            state.outcome=SlotOutcome.shipped
            return state.model_dump(mode="json")
        reason=None
        try:
            if state.slot.slot_id not in self.visuals:
                self.visuals[state.slot.slot_id]=VisualAgent(self.root,self.client("visual",state.slot.slot_id))
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
        await self.image_count("image_calls")
        try:
            await asyncio.to_thread(self.renderer,spec,path)
            from PIL import Image
            with Image.open(path) as image:
                if image.format!="PNG" or image.size!=(1200,675): raise ValueError("invalid rendered card")
                image.verify()
            await self.image_count("pillow_cards")
            backend="pillow_card"
        except Exception:
            path=spec_path
            backend="spec_json"
            reason="pillow_fallback_spec"
        state.image=ImageResult(slot_id=state.slot.slot_id,path=path.relative_to(self.context.run_dir).as_posix(),backend=backend,fallback_reason=reason)
        state.outcome=SlotOutcome.shipped
        await self.record("visuals",VisualRecord(record_id=record_id,slot_id=state.slot.slot_id,attempt=state.attempt,spec=spec,result=state.image,input_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest()))
        return state.model_dump(mode="json")

    async def collect(self,raw):
        state=lane_state(raw)
        if state.outcome is None: raise ValueError("nonterminal slot at join")
        if state.outcome==SlotOutcome.shipped:
            piece=FinalPiece(record_id=f"{state.slot.slot_id}/final/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,topic_id=state.pack.topic_id,topic_name=state.pack.topic_name,draft=state.audit.approved(),image=state.image)
            await self.record("final",piece)
        return {"slots":{state.slot.slot_id:state.model_dump(mode="json")}}

    async def export(self,week):
        states=[SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        accepted=[]
        for state in states:
            if state.outcome==SlotOutcome.shipped:
                accepted.append(ExportPost(record_id=f"{state.slot.slot_id}/export/{state.attempt}/1",slot_id=state.slot.slot_id,attempt=state.attempt,type=state.slot.type,topic_id=state.pack.topic_id,topic_name=state.pack.topic_name,tweets=[tweet.text for tweet in state.audit.approved().tweets],image=state.image.path).model_dump(mode="json"))
        export_posts(self.context.run_dir,accepted)
        return {}

    async def advise(self,week):
        status="not_enabled"
        reason=None
        output=None
        patch=""
        input_hash=None
        if self.context.settings.advisor_enabled:
            try:
                evidence={name:json.loads(self.trails[name].path.read_text(encoding="utf-8")) for name in ["research_log","drafts","review_report","final"]}
                request=AdvisorRequest(evidence=evidence,instructions=instruction_files(self.root,self.plan))
                input_hash=hashlib.sha256(request.model_dump_json().encode()).hexdigest()
                saved=self.saved_record("advisor","_run/advisor/1/1",AdvisorRecord,request)
                if saved:
                    if saved.output is not None: advice_patch(request,saved.output)
                    local_file(self.context.run_dir,"advice.md")
                    local_file(self.context.run_dir,"advice.patch")
                    return {"advisor":saved.status,"advisor_reason":saved.reason}
                output=await Advisor(self.root,self.client("advisor","_run"))(request)
                patch=advice_patch(request,output)
                status="completed"
            except Exception as error:
                status="failed"
                reason=failure(error).value
        (self.context.run_dir/"advice.md").write_text("# Run advice\n\n"+("\n".join("- "+line for line in output.recommendations) if status=="completed" else "Advisor "+status+(": "+reason if reason else ""))+"\n",encoding="utf-8")
        (self.context.run_dir/"advice.patch").write_text(patch,encoding="utf-8")
        await self.record("advisor",AdvisorRecord(record_id="_run/advisor/1/1",slot_id="_run",attempt=1,status=status,output=output if status=="completed" else None,reason=reason,input_sha256=input_hash))
        return {"advisor":status,"advisor_reason":reason}

    async def finalize(self,week):
        states=[SlotState.model_validate(week["slots"][slot.slot_id]) for slot in self.plan]
        accepted=[state for state in states if state.outcome==SlotOutcome.shipped]
        totals=self.context.ledger.totals()
        usage=[json.loads(line) for line in (self.context.run_dir/"usage.jsonl").read_text(encoding="utf-8").splitlines()]
        summary=RunSummary(record_id="_run/summary/1/1",slot_id="_run",attempt=1,run_id=self.context.identity.run_id if self.context.identity else self.context.run_dir.name,slots={state.slot.slot_id:TerminalSlot(outcome=state.outcome,attempts=state.attempt,topic_id=state.slot.topic_id,reasons=state.reasons) for state in states},shipped=len(accepted),confirmed_tokens=totals["tokens"]-totals["uncertain_tokens"],uncertain_tokens=totals["uncertain_tokens"],confirmed_cost_usd=totals["cost_usd"]-totals["uncertain_cost_usd"],uncertain_cost_usd=totals["uncertain_cost_usd"],unpriced=any(record["cost_source"]=="unknown" for record in usage) or any("unpriced_model" in state.reasons for state in states),external_usage=self.external,advisor=week["advisor"],advisor_reason=week.get("advisor_reason"))
        (self.context.run_dir/"run_summary.json").write_text(summary.model_dump_json(indent=2),encoding="utf-8")
        (self.context.run_dir/"run.log").write_text("export: complete\nsummary: complete\n",encoding="utf-8")
        return {"summary":summary.model_dump(mode="json")}

    def graph(self,checkpointer,interrupt_after=None):
        interrupts=interrupt_after or []
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
        graph.add_node("lane",lane.compile(interrupt_after=[name.split(":",1)[1] for name in interrupts if name.startswith("lane:")]))
        graph.add_node("export",self.export)
        graph.add_node("advisor",self.advise)
        graph.add_node("summary",self.finalize)
        graph.add_edge(START,"focused_research")
        graph.add_edge("focused_research","topic_selection")
        graph.add_conditional_edges("topic_selection",lambda state:[Send("lane",state["slots"][slot.slot_id]) for slot in self.plan])
        graph.add_edge("lane","export")
        graph.add_edge("export","advisor")
        graph.add_edge("advisor","summary")
        graph.add_edge("summary",END)
        return graph.compile(checkpointer=checkpointer,interrupt_after=[name for name in interrupts if not name.startswith("lane:")])


class RunInterrupted(RuntimeError):
    """A deliberately interrupted graph has durable unfinished nodes."""


async def run_pipeline(root: Path,slots: list[ResolvedSlot] | None=None,output: Path | None=None,settings: Settings | None=None,model_factory=None,search_factory=None,renderer=None,*,resume: Path | None=None,budget_overrides=None,interrupt_after=None) -> RunSummary:
    root=root.resolve()
    if resume is not None:
        if slots is not None or output is not None or settings is not None:
            raise ValueError("resume uses saved plan/settings; only budget increases allowed")
        run_dir=resume.resolve()
        snapshot_root,slots,settings,identity=resume_inputs(run_dir,budget_overrides)
        validate_inputs(snapshot_root,slots,settings)
    else:
        if budget_overrides: raise ValueError("budget overrides are for resume")
        settings=settings or Settings.load(root)
        validate_inputs(root,slots,settings)
        run_dir=(output or root/"output"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid4().hex[:8])).resolve()
        if run_dir.exists(): raise ValueError("output run already exists; choose a new directory")
        run_dir.mkdir(parents=True,exist_ok=False)
    with RunLock(run_dir,create=resume is None):
        if resume is None:
            snapshot_root=snapshot(root,run_dir,slots,settings)
            identity=RunIdentity(thread_id=uuid4().hex,run_id=run_dir.name,snapshot_files=snapshot_hashes(snapshot_root))
            atomic_json(run_dir/"run_identity.json",identity.model_dump())
            atomic_json(run_dir/"runtime-settings.json",settings.model_dump())
            atomic_json(run_dir/"external-usage.json",ExternalUsage().model_dump())
            (run_dir/"usage.jsonl").write_text("",encoding="utf-8")
        else:
            snapshot_root,slots,settings,identity=resume_inputs(run_dir,budget_overrides)
        context=RunContext(snapshot_root,root,run_dir,settings,None,None,asyncio.Lock(),identity)
        pipeline=Pipeline(context,slots,model_factory or default_model_factory,search_factory or configured_web_search,renderer or render_card)
        async with AsyncSqliteSaver.from_conn_string(str(run_dir/"checkpoint.sqlite")) as saver:
            graph=pipeline.graph(saver,interrupt_after)
            config={"configurable":{"thread_id":identity.thread_id},"recursion_limit":50}
            if resume is not None:
                saved=await graph.aget_state(config)
                state=WeekSnapshot.model_validate(saved.values)
                if list(state.slots)!=[slot.slot_id for slot in slots]: raise ValueError("checkpoint plan mismatch")
                if not saved.next and state.summary is not None:
                    return state.summary
                if not saved.next: raise ValueError("checkpoint has no unfinished nodes")
            context.ledger=Ledger(run_dir/"ledger.json",settings.run_token_budget,settings.run_cost_budget_usd)
            atomic_json(run_dir/"runtime-settings.json",settings.model_dump())
            context.prices=Prices(update=model_factory is None)
            try:
                result=await graph.ainvoke(None if resume is not None else {"slots":{slot.slot_id:SlotState(slot=slot).model_dump(mode="json") for slot in slots},"warnings":[]},config)
                if "summary" not in result: raise RunInterrupted("graph interrupted; resume saved run")
                return RunSummary.model_validate(result["summary"])
            finally:
                saved=await graph.aget_state(config)
                atomic_json(run_dir/"state.json",WeekSnapshot.model_validate(saved.values).model_dump(mode="json"))
                context.prices.close()
