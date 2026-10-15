import asyncio
import csv
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from content_pipeline.config import Settings, load_brand, load_type, load_plan, load_agent, resolve_model, load_prompt
from content_pipeline.models import Draft, Tweet, SourceRecord, ResearchPack, Review
from content_pipeline.validation import validate
from content_pipeline.research import KnowledgeBase, WebSearch, public_url, assign_topics
from content_pipeline.ledger import Ledger, BudgetExhausted
from content_pipeline.model_client import ModelClient, ModelFailure, Prices
from content_pipeline.trail import Trail, export_posts
from content_pipeline.schemas import export
from content_pipeline.tools import research_tools

ROOT = Path(__file__).resolve().parents[1]


def pack():
    return ResearchPack(slot_id="thread_1",topic_id="KB-01",topic_name="Reentrancy",sources=[SourceRecord(source_id="KB-01",kind="kb",title="Reentrancy",evidence_text="External calls can reenter before state is updated.")])


def draft(text="State before calls.", ids=None, count=5):
    return Draft(slot_id="thread_1",tweets=[Tweet(text=text,source_ids=ids if ids is not None else ["KB-01"]) for _ in range(count)])


def validate_thread(value, research=None):
    return validate(value,research or pack(),load_type(ROOT,"tech-thread"),load_brand(ROOT/"inputs/brand-voice.md")[0])


def test_loaded_inputs_and_invalid_configuration(tmp_path):
    slots = load_plan(ROOT, ROOT/"inputs/plan.json")
    assert [(s.slot_id,s.type,s.focus) for s in slots] == [("thread_1","tech-thread","reentrancy"),("thread_2","tech-thread",None),("thread_3","tech-thread",None),("marketing_1","marketing-post",None),("marketing_2","marketing-post",None),("marketing_3","marketing-post",None)]
    with pytest.raises(ValueError,match="missing configuration"):
        load_type(ROOT,"absent")
    with pytest.raises(ValidationError):
        Settings(max_tweet_chars=281)
    assert Settings.load(tmp_path, {"max_tweet_chars": 240}).max_tweet_chars == 240
    assert load_agent(ROOT,"thread_writer").skills == ["brand_rules"]
    assert load_agent(ROOT,"research").skills == ["search_kb","web_search"]
    agent=load_agent(ROOT,"reviewer")
    agent.model="openai:agent-choice"
    assert resolve_model(Settings(model_override="openai:override"),agent)=="openai:override"
    assert resolve_model(Settings(),agent)=="openai:agent-choice"
    prompt=load_prompt(ROOT,"thread_writer","tech-thread")
    assert "Return unnumbered text" in prompt
    assert "Sentinel Grove" in prompt
    assert "Shared by writers" in prompt


def test_schema_export_matches_models(tmp_path):
    export(tmp_path)
    assert json.loads((tmp_path/"ResearchPack.json").read_text()) == ResearchPack.model_json_schema()
    assert json.loads((tmp_path/"Draft.json").read_text())["additionalProperties"] is False


def test_folder_only_post_type(tmp_path):
    shutil.copytree(ROOT/"post_types",tmp_path/"post_types")
    shutil.copytree(ROOT/"agents",tmp_path/"agents")
    shutil.copytree(tmp_path/"post_types/marketing-post",tmp_path/"post_types/case-study")
    type_file=tmp_path/"post_types/case-study/type.yaml"
    type_file.write_text(type_file.read_text().replace("name: marketing-post","name: case-study"))
    value = Draft(slot_id="thread_1",tweets=[Tweet(text="Explain one root cause.",source_ids=["KB-01"])])
    result = validate(value,pack(),load_type(tmp_path,"case-study"),load_brand(ROOT/"inputs/brand-voice.md")[0])
    assert result.model_dump()["normalized"]["tweets"] == [{"text":"Explain one root cause.","source_ids":["KB-01"]}]
    assert result.accepted is True


def test_normalization_keeps_original_and_numbers_before_review():
    result = validate_thread(draft("  “State”   before calls. #audit #security  "))
    assert result.original.tweets[0].text == "  “State”   before calls. #audit #security  "
    assert [t.text for t in result.normalized.tweets] == [f'{i}/5 "State" before calls.' for i in range(1,6)]
    assert result.fixes[:3] == ["tweet_1:whitespace","tweet_1:curly_quotes","tweet_1:trailing_hashtags"]
    assert result.accepted is True


@pytest.mark.parametrize("text,error",[("Guaranteed safety.","banned_phrase:guaranteed"),("Use #audit inside text.","hashtag"),("https://sub.example.com/path","link_domain:sub.example.com"),("界"*139,"length"),("a"*277,"length")])
def test_brand_and_weighted_length_rejections(text,error):
    result=validate_thread(draft(text))
    assert result.accepted is False
    assert "tweet_1:"+error in result.violations


def test_boundaries_and_url_weight_are_accepted():
    result=validate_thread(draft("unguaranteed https://example.com/"+"x"*400))
    assert result.normalized.tweets[0].text == "1/5 unguaranteed https://example.com/"+"x"*400
    assert result.accepted is True


def test_citations_unknown_failed_and_snippet_rejected():
    evidence=pack()
    evidence.sources += [SourceRecord(source_id="WEB-ab",kind="web",title="Snippet",evidence_text="snippet",status="snippet_only"),SourceRecord(source_id="WEB-cd",kind="web",title="Failed",evidence_text="",status="fetch_failed")]
    result=validate_thread(draft(ids=["KB-99","WEB-ab","WEB-cd"]),evidence)
    assert result.violations[:3] == ["tweet_1:citation:KB-99","tweet_1:citation:WEB-ab","tweet_1:citation:WEB-cd"]
    assert result.accepted is False


def test_citation_acceptance_guard():
    result=validate_thread(draft(ids=["KB-99"]))
    assert result.accepted is False
    assert result.violations[0] == "tweet_1:citation:KB-99"


def test_kb_alias_and_batch_assignment():
    kb=KnowledgeBase(ROOT/"inputs/kb.json")
    assert kb.search("recursive call")[0]["id"] == "KB-01"
    assert kb.source("KB-01").evidence_text == "External calls can reenter before state is finalized. Calling another contract hands it control, allowing it to call back before the first call returns. In a withdrawal that sends Ether before clearing the caller balance, a callback can withdraw against the still-unchanged balance again. Reentrancy can arise from calls to other contracts, not only Ether transfers; dependencies across multiple contracts also matter.\nRoot cause: State updates after untrusted calls allow repeated execution. Apply checks-effects-interactions: validate inputs and permissions, update the relevant storage state, then make external calls. In the withdrawal example, save the withdrawal amount and clear the stored balance before calling the recipient; require the call to succeed so failure reverts the operation. Review other callable paths and contracts sharing the affected state, since a callback may enter through another function or dependency."
    slots=load_plan(ROOT,ROOT/"inputs/plan.json")
    slots[0].topic_id="KB-01"
    assigned,warnings=assign_topics(ROOT,slots,{"thread_2":"KB-01","thread_3":"KB-01","marketing_1":"ANGLE-service","marketing_2":"ANGLE-service"},kb)
    assert assigned["thread_1"] == "KB-01"
    assert len(set(assigned.values())) == 6
    assert [assigned[f"marketing_{i}"] for i in range(1,4)] == ["ANGLE-service","ANGLE-credibility","ANGLE-education"]
    assert warnings == []
    slots[1].focus="recursive call"
    slots[1].topic_id="KB-01"
    assigned,warnings=assign_topics(ROOT,slots,{},kb,[ [e["id"] for e in kb.entries] ])
    assert assigned["thread_2"] == "KB-01"
    assert warnings == ["explicit_duplicate:KB-01","history_exhausted:thread_3"]


class Search:
    def __init__(self,hits): self.hits=hits
    def search(self,**kwargs):
        assert kwargs["include_raw_content"] is True
        return {"results":self.hits}
    def text(self,*args,**kwargs): return self.hits


def test_web_raw_content_and_fetched_evidence():
    service=WebSearch(tavily=Search([{"url":"https://example.com/a","title":"A","content":"summary","raw_content":"actual page evidence"}]),check_url=lambda u:u,fetch=lambda u:pytest.fail("raw page must avoid fetch"))
    records=service.search("reentrancy")
    assert [(r.evidence_text,r.status) for r in records] == [("actual page evidence","ok")]
    assert (service.calls,service.tavily_credits) == (1,1)
    fallback=WebSearch(tavily=Search([]),ddgs=Search([{"href":"https://example.com/b","title":"B","body":"snippet"}]),check_url=lambda u:u,fetch=lambda u:"retained full page")
    assert fallback.search("topic")[0].evidence_text == "retained full page"


def test_web_summary_and_failed_fetch_cannot_be_cited():
    def fail(url): raise TimeoutError("secret must not appear")
    service=WebSearch(tavily=Search([{"url":"https://example.com/a","content":"not page evidence"}]),check_url=lambda u:u,fetch=fail)
    records=service.search("topic")
    assert [(r.evidence_text,r.status) for r in records] == [("","fetch_failed")]
    with pytest.raises(ValidationError,match="no citable evidence"):
        ResearchPack(slot_id="x",topic_id="y",topic_name="Topic",sources=records)
    for _ in range(3): service.search("topic")
    with pytest.raises(ValueError,match="search-step limit"): service.search("topic")


@pytest.mark.parametrize("url",["file:///etc/passwd","http://localhost/a","http://127.0.0.1/a","http://169.254.169.254/","http://10.0.0.1/","https://user:pass@example.com/"])
def test_public_url_rejects_nonpublic_targets(url):
    def resolver(host,port,**kwargs): return [(None,None,None,None,(host,port))]
    with pytest.raises(ValueError): public_url(url,resolver)


def test_public_url_accepts_public_and_rejects_mixed_dns():
    def resolver(host,port,**kwargs): return [(None,None,None,None,("93.184.216.34",port))]
    assert public_url("https://example.com/a",resolver)=="https://example.com/a"
    def mixed(host,port,**kwargs): return resolver(host,port)+[(None,None,None,None,("127.0.0.1",port))]
    with pytest.raises(ValueError,match="private target"): public_url("https://example.com/",mixed)


def test_fetch_redirect_to_private_is_blocked(monkeypatch):
    import content_pipeline.research as research
    import http.client
    original=research.public_url
    def resolver(host,port,**kwargs): return [(None,None,None,None,("127.0.0.1" if host=="127.0.0.1" else "93.184.216.34",port))]
    monkeypatch.setattr(research.socket,"getaddrinfo",resolver)
    monkeypatch.setattr(research,"public_url",lambda url:original(url,resolver))
    connections=[]
    class Connection:
        def __init__(self,*args,**kwargs): connections.append(args)
        def request(self,*args,**kwargs): pass
        def getresponse(self): return SimpleNamespace(status=302,getheader=lambda key:"http://127.0.0.1/private")
        def close(self): pass
    monkeypatch.setattr(http.client,"HTTPConnection",Connection)
    monkeypatch.setattr(research.socket,"create_connection",lambda *args,**kwargs:object())
    with pytest.raises(ValueError,match="private target"): research.fetch_page("http://example.com/a")
    assert connections==[("example.com",80)]


def test_research_llm_callable_tool_returns_retained_entry():
    tools=research_tools(ROOT,KnowledgeBase(ROOT/"inputs/kb.json"),WebSearch())
    assert [tool.name for tool in tools]==["search_kb","web_search"]
    assert tools[0].invoke({"query":"recursive call"})[0]["name"]=="Reentrancy"


async def test_concurrent_budget_and_crash_recovery(tmp_path):
    ledger=Ledger(tmp_path/"ledger.json",100,1)
    results=await asyncio.gather(ledger.reserve("a",70,.7),ledger.reserve("b",70,.7),return_exceptions=True)
    assert sum(isinstance(r,BudgetExhausted) for r in results)==1
    await ledger.dispatch("a")
    recovered=Ledger(tmp_path/"ledger.json",100,1)
    assert recovered.totals()=={"tokens":70,"cost_usd":.7,"uncertain_tokens":70,"uncertain_cost_usd":.7}
    with pytest.raises(BudgetExhausted): await recovered.reserve("c",31,.1)
    await recovered.settle("a",20,.2)
    assert recovered.totals()=={"tokens":20,"cost_usd":.2,"uncertain_tokens":0,"uncertain_cost_usd":0}


class FixedPrices:
    def quote(self,model,inputs,outputs,cached=0,cache_creation=0): return (inputs+outputs)*.000001,"fixture-price"


def response(parsed=None,error=None,finish="stop",provider_cost=None):
    metadata={"finish_reason":finish}
    if provider_cost is not None: metadata["token_usage"]={"cost":provider_cost,"cost_details":{"upstream_inference_cost":.02}}
    return {"raw":SimpleNamespace(content="structured",usage_metadata={"input_tokens":100,"output_tokens":30,"input_token_details":{"cache_read":20},"output_token_details":{"reasoning":10}},response_metadata=metadata),"parsed":parsed,"parsing_error":error}


class Transport:
    def __init__(self,results): self.results=list(results); self.payloads=[]
    async def __call__(self,messages,schema,max_tokens):
        self.payloads.append((messages,max_tokens))
        result=self.results.pop(0)
        if isinstance(result,Exception): raise result
        return result


def client(tmp_path,transport,**kwargs):
    return ModelClient("openai:gpt-4.1-mini",Ledger(tmp_path/"ledger.json",100000,10),tmp_path/"usage.jsonl",transport,prices=FixedPrices(),**kwargs)


async def test_structured_repair_truncation_and_provider_cost(tmp_path):
    transport=Transport([response(error=ValueError("bad")),response(finish="length"),response(draft().model_dump(),provider_cost=.01)])
    model=client(tmp_path,transport)
    output=await model.call(Draft,[{"role":"user","content":"write"}],"thread_1","writer",1,100)
    assert output.tweets[0].text == "State before calls."
    assert [p[1] for p in transport.payloads] == [100,100,150]
    assert "Parse error:" in transport.payloads[1][0][-1]["content"]
    usage=[json.loads(line) for line in (tmp_path/"usage.jsonl").read_text().splitlines()]
    assert [u["call_id"] for u in usage]==["thread_1/writer/1/1","thread_1/writer/1/2","thread_1/writer/1/3"]
    assert usage[-1]["cost_usd"] == .03
    assert usage[-1]["cost_source"] == "provider"
    assert (usage[-1]["input_tokens"],usage[-1]["output_tokens"],usage[-1]["cached_input_tokens"],usage[-1]["reasoning_tokens"])==(100,30,20,10)
    assert model.ledger.totals()["tokens"] == 390


async def test_malformed_reviewer_fails_closed_and_bills_both(tmp_path):
    model=client(tmp_path,Transport([response(error=ValueError("bad")),response(error=ValueError("bad"))]))
    with pytest.raises(ModelFailure,match="failed_output"):
        await model.call(Review,[{"role":"user","content":"review"}],"x","reviewer",1,100)
    assert model.ledger.totals()["tokens"] == 260
    assert len((tmp_path/"usage.jsonl").read_text().splitlines()) == 2


async def test_transient_retries_retain_uncertain_charge_and_sanitize_error(tmp_path):
    waits=[]
    async def sleep(seconds): waits.append(seconds)
    transport=Transport([TimeoutError("API_KEY=secret")]*4)
    model=client(tmp_path,transport,sleep=sleep)
    with pytest.raises(ModelFailure,match="failed_api"):
        await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",1,100)
    assert waits==[1,2,4]
    assert model.ledger.totals()["uncertain_tokens"]==408
    assert "secret" not in (tmp_path/"usage.jsonl").read_text()


async def test_permanent_provider_budget_failure_does_not_retry(tmp_path):
    error=RuntimeError("secret")
    error.status_code=403
    transport=Transport([error])
    model=client(tmp_path,transport)
    with pytest.raises(ModelFailure,match="failed_budget"):
        await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",1,100)
    assert len(transport.payloads)==1
    assert model.ledger.totals()["uncertain_tokens"]==102


async def test_budget_refusal_precedes_dispatch(tmp_path):
    transport=Transport([])
    model=client(tmp_path,transport)
    model.ledger.token_budget=5
    with pytest.raises(BudgetExhausted):
        await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",1,100)
    assert transport.payloads==[]
    assert model.ledger.totals()["tokens"]==0


async def test_usage_estimate_and_actual_overshoot(tmp_path):
    transport=Transport([{"raw":SimpleNamespace(content="abcdefgh",usage_metadata=None,response_metadata={}),"parsed":draft().model_dump(),"parsing_error":None}])
    model=client(tmp_path,transport)
    await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",1,100)
    usage=json.loads((tmp_path/"usage.jsonl").read_text())
    assert (usage["input_tokens"],usage["output_tokens"],usage["estimated"])==(2,3,True)
    transport=Transport([response(draft().model_dump())])
    model.transport=transport
    await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",2,10)
    usage=json.loads((tmp_path/"usage.jsonl").read_text().splitlines()[-1])
    assert usage["overshoot_tokens"]==118
    assert model.ledger.totals()["tokens"]==135


def test_real_price_cache_normalization_and_unknown():
    prices=Prices(update=False)
    assert prices.quote("openai:gpt-4.1-mini",100,10,20)[0] == pytest.approx(.00005)
    with pytest.raises(LookupError): prices.quote("openai:nonexistent-model-for-test",100,10)


async def test_unpriced_refused_or_token_only_opt_in(tmp_path):
    class Unknown:
        def quote(self,*args): raise LookupError()
    transport=Transport([response(draft().model_dump())])
    model=client(tmp_path,transport)
    model.prices=Unknown()
    with pytest.raises(ModelFailure,match="unpriced_model"):
        await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",1,100)
    assert transport.payloads==[]
    model.allow_unpriced=True
    output=await model.call(Draft,[{"role":"user","content":"write"}],"x","writer",1,100)
    assert output.tweets[0].text=="State before calls."
    assert json.loads((tmp_path/"usage.jsonl").read_text())["cost_source"]=="unknown"


async def test_concurrent_trail_and_csv_exact_roundtrip(tmp_path):
    trail=Trail(tmp_path/"drafts.json")
    await asyncio.gather(*(trail.append(f"x/writer/1/{i}",{"text":str(i)}) for i in range(10)))
    await trail.append("x/writer/1/1",{"text":"duplicate"})
    assert [(r["record_id"],r["text"]) for r in json.loads((tmp_path/"drafts.json").read_text())]==[(f"x/writer/1/{i}",str(i)) for i in range(10)]
    export_posts(tmp_path,[{"tweets":['1/2 Say "hello", precisely.','2/2 State before calls.'],"image":"images/x.png"}])
    with (tmp_path/"posts.csv").open(newline="",encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))==[{"Post Text":'1/2 Say "hello", precisely.\n2/2 State before calls.',"Image":"images/x.png"}]
    export_posts(tmp_path,[])
    assert (tmp_path/"posts.csv").read_bytes()==b"Post Text,Image\r\n"
