"""Real SDK boundary regressions; HTTP is handled by local mock transports."""
import asyncio
import csv
import importlib.util
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
import yaml
from langchain_openai import ChatOpenAI
from content_pipeline.config import Settings, load_agent, load_brand, load_prompt, load_type
from content_pipeline.ledger import Ledger
from content_pipeline.model_client import LangChainTransport, ModelClient, ModelFailure, Prices, configured_client
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.trail import export_posts
from content_pipeline.validation import validate

ROOT=Path(__file__).resolve().parents[1]


class FixedPrices:
    def quote(self,*args): return .001,"offline-fixture"


def new_client(tmp_path,transport,**kwargs):
    return ModelClient("openai:gpt-4.1-mini",Ledger(tmp_path/"ledger.json",100000,10),tmp_path/"usage.jsonl",transport=transport,prices=FixedPrices(),**kwargs)


def rows(tmp_path):
    return [json.loads(line) for line in (tmp_path/"usage.jsonl").read_text().splitlines()]


def valid_draft():
    return {"slot_id":"m","attempt":1,"tweets":[{"text":"State before calls.","source_ids":["KB-01"]}]}


def completion(text,finish="stop",tokens=100,valid_tool=False):
    message={"role":"assistant","content":text}
    if valid_tool:
        message["tool_calls"]=[{"id":"call_1","type":"function","function":{"name":"Draft","arguments":json.dumps(valid_draft())}}]
    return {"id":"chatcmpl-offline","object":"chat.completion","created":1,"model":"gpt-4.1-mini","choices":[{"index":0,"message":message,"finish_reason":finish}],"usage":{"prompt_tokens":10,"completion_tokens":tokens,"total_tokens":10+tokens}}


def real_sdk(tmp_path,monkeypatch,handler):
    monkeypatch.setenv("OPENAI_API_KEY","synthetic-openai")
    original_init=ChatOpenAI.__init__
    def initialize(self,*args,**kwargs):
        kwargs["http_async_client"]=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        original_init(self,*args,**kwargs)
    monkeypatch.setattr(ChatOpenAI,"__init__",initialize)
    return LangChainTransport("openai:gpt-4.1-mini",root=tmp_path)


@pytest.mark.parametrize("kind",["timeout","connection"])
@pytest.mark.parametrize("recover",[True,False])
async def test_sdk_connection_retry_with_success(tmp_path,monkeypatch,kind,recover):
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests)<=3 or not recover:
            if kind=="timeout": raise httpx.ReadTimeout("synthetic timeout",request=request)
            raise httpx.ConnectError("synthetic connection",request=request)
        return httpx.Response(200,json=completion(json.dumps(valid_draft()),tokens=20,valid_tool=True))
    waits=[]
    async def sleep(seconds): waits.append(seconds)
    model=new_client(tmp_path,real_sdk(tmp_path,monkeypatch,handler),sleep=sleep)
    if recover:
        output=await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
        assert output.tweets[0].text=="State before calls."
    else:
        with pytest.raises(ModelFailure,match="failed_api"):
            await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert len(requests)==4
    assert waits==[1,2,4]
    assert [r["status"] for r in rows(tmp_path)]==["uncertain"]*3+["confirmed" if recover else "uncertain"]
    assert model.ledger.totals()["uncertain_tokens"]==(306 if recover else 408)
    assert model.ledger.totals()["tokens"]==(336 if recover else 408)


async def test_sdk_truncation_preserves_usage_and_retries_larger(tmp_path,monkeypatch):
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        payload=completion('{"slot_id":',finish="length") if len(requests)==1 else completion(json.dumps(valid_draft()),tokens=20,valid_tool=True)
        return httpx.Response(200,json=payload)
    model=new_client(tmp_path,real_sdk(tmp_path,monkeypatch,handler))
    output=await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert output.slot_id=="m"
    assert [r.get("max_completion_tokens",r.get("max_tokens")) for r in requests]==[100,150]
    assert [(r["input_tokens"],r["output_tokens"],r["status"]) for r in rows(tmp_path)]==[(10,100,"confirmed"),(10,20,"confirmed")]
    assert model.ledger.totals()["tokens"]==140
    assert model.ledger.totals()["uncertain_tokens"]==0


@pytest.mark.parametrize("second_valid",[True,False])
async def test_sdk_malformed_response_repairs_once_with_actual_usage(tmp_path,monkeypatch,second_valid):
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        good=len(requests)==2 and second_valid
        return httpx.Response(200,json=completion(json.dumps(valid_draft()) if good else "not json",valid_tool=good))
    model=new_client(tmp_path,real_sdk(tmp_path,monkeypatch,handler))
    if second_valid:
        output=await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
        assert output.tweets[0].text=="State before calls."
    else:
        with pytest.raises(ModelFailure,match="failed_output"):
            await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert len(requests)==2
    assert "Parse error:" in requests[1]["messages"][-1]["content"]
    assert [(r["input_tokens"],r["output_tokens"],r["estimated"],r["status"]) for r in rows(tmp_path)]==[(10,100,False,"confirmed")]*2
    assert model.ledger.totals()["tokens"]==220


@pytest.mark.parametrize("parseable",[True,False])
async def test_gemini_max_tokens_is_truncation_even_if_parseable(tmp_path,parseable):
    from google.genai.types import GenerateContentResponse, GenerateContentResponseUsageMetadata, Candidate, Content, Part, FinishReason
    from langchain_google_genai.chat_models import _response_to_result
    caps=[]
    async def transport(messages,schema,max_tokens):
        caps.append(max_tokens)
        first=len(caps)==1
        response=GenerateContentResponse(candidates=[Candidate(content=Content(role="model",parts=[Part(text=json.dumps(valid_draft()))]),finish_reason=FinishReason.MAX_TOKENS if first else FinishReason.STOP)],usage_metadata=GenerateContentResponseUsageMetadata(prompt_token_count=10,candidates_token_count=100 if first else 20,total_token_count=110 if first else 30))
        generation=_response_to_result(response).generations[0]
        generation.message.response_metadata.update(generation.generation_info)
        return {"raw":generation.message,"parsed":valid_draft() if parseable or not first else None,"parsing_error":None}
    model=new_client(tmp_path,transport)
    output=await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert output.slot_id=="m"
    assert caps==[100,150]
    assert model.ledger.totals()["tokens"]==140


def test_opencode_bare_model_id_has_actual_bundled_price():
    prices=Prices(update=False)
    assert prices.quote("opencode:gpt-5",100,30)==(.000425,"genai-prices@0.1.11")


def test_declared_anthropic_adapter_initializes_without_request(tmp_path,monkeypatch):
    import tomllib
    dependencies=tomllib.loads((ROOT/"pyproject.toml").read_text())["project"]["dependencies"]
    assert any(value.startswith("langchain-anthropic") for value in dependencies)
    assert importlib.util.find_spec("langchain_anthropic") is not None
    from langchain.chat_models import init_chat_model
    chat=init_chat_model("claude-sonnet-4-5",model_provider="anthropic",api_key="synthetic-unused-key",max_tokens=100,max_retries=0)
    assert chat.model=="claude-sonnet-4-5"
    assert chat.max_tokens==100
    assert chat.with_structured_output(Draft,include_raw=True) is not None
    monkeypatch.setenv("ANTHROPIC_API_KEY","synthetic-unused-key")
    # Preparing constructs clients and schema bindings; it never invokes a model.
    assert LangChainTransport("anthropic:claude-sonnet-4-5",root=tmp_path).prepare(Draft,100) is not None


@pytest.mark.parametrize("text",[" ","a\uffffb"])
def test_normalized_empty_or_parser_invalid_tweet_rejected(text):
    source=SourceRecord(source_id="KB-01",kind="kb",title="Topic",evidence_text="Evidence")
    pack=ResearchPack(slot_id="m",topic_id="KB-01",topic_name="Topic",sources=[source])
    draft=Draft(slot_id="m",tweets=[Tweet(text=text,source_ids=["KB-01"])])
    result=validate(draft,pack,load_type(ROOT,"marketing-post"),load_brand(ROOT/"inputs/brand-voice.md")[0])
    assert result.accepted is False
    assert "tweet_1:invalid_text" in result.violations
    assert result.original.tweets[0].text==text


@pytest.mark.parametrize("gateway",["openrouter","opencode"])
@pytest.mark.parametrize("missing",[None,""])
async def test_gateway_missing_key_fails_before_sdk_dispatch_and_charge(tmp_path,monkeypatch,gateway,missing):
    monkeypatch.setenv("OPENAI_API_KEY","synthetic-openai")
    key="OPENROUTER_API_KEY" if gateway=="openrouter" else "OPENCODE_API_KEY"
    if missing is None: monkeypatch.delenv(key,raising=False)
    else: monkeypatch.setenv(key,missing)
    dispatched=[]
    original=ChatOpenAI.with_structured_output
    def structured(chat,schema,**kwargs):
        dispatched.append(chat.openai_api_key.get_secret_value()=="synthetic-openai")
        return SimpleNamespace(ainvoke=blocked)
    async def blocked(messages): return {"offline":True}
    monkeypatch.setattr(ChatOpenAI,"with_structured_output",structured)
    transport=LangChainTransport(gateway+":gpt-5",root=tmp_path)
    model=new_client(tmp_path,transport)
    with pytest.raises(ModelFailure,match="failed_api"):
        await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert dispatched==[]
    assert model.ledger.totals()=={"tokens":0,"cost_usd":0,"uncertain_tokens":0,"uncertain_cost_usd":0}
    assert not (tmp_path/"usage.jsonl").exists()


async def test_sdk_initialization_failure_creates_no_billed_reservation(tmp_path,monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","synthetic-openai")
    def fail(*args,**kwargs): raise ValueError("synthetic initialization failure")
    monkeypatch.setattr(ChatOpenAI,"__init__",fail)
    model=new_client(tmp_path,LangChainTransport("openai:gpt-4.1-mini",root=tmp_path))
    with pytest.raises(ModelFailure,match="failed_api"):
        await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert model.ledger.totals()=={"tokens":0,"cost_usd":0,"uncertain_tokens":0,"uncertain_cost_usd":0}
    assert not (tmp_path/"usage.jsonl").exists()


@pytest.mark.parametrize("gateway",["openrouter","opencode"])
async def test_configured_gateway_key_wins_over_ambient_openai(tmp_path,monkeypatch,gateway):
    monkeypatch.setenv("OPENAI_API_KEY","synthetic-unrelated")
    monkeypatch.setenv("OPENROUTER_API_KEY" if gateway=="openrouter" else "OPENCODE_API_KEY","synthetic-gateway")
    captured=[]
    def handler(request):
        captured.append((request.url.host,request.headers["authorization"]=="Bearer synthetic-gateway",json.loads(request.content)["model"]))
        return httpx.Response(200,json=completion(json.dumps(valid_draft()),tokens=20,valid_tool=True))
    real_sdk(tmp_path,monkeypatch,handler)
    model=new_client(tmp_path,LangChainTransport(gateway+":gpt-5",root=tmp_path))
    output=await model.call(Draft,[{"role":"user","content":"write"}],"m","writer",1,100)
    assert output.slot_id=="m"
    assert captured==[("openrouter.ai" if gateway=="openrouter" else "opencode.ai",True,"gpt-5")]


@pytest.mark.parametrize("image",["C:outside.png","/outside.png","\\outside.png","C:\\outside.png","\\\\server\\share\\outside.png","../outside.png","images/../../outside.png"])
def test_image_export_rejects_every_anchored_or_traversing_path(tmp_path,image):
    with pytest.raises(ValueError,match="relative"):
        export_posts(tmp_path,[{"tweets":["approved"],"image":image}])


def test_image_export_accepts_run_relative_windows_separator(tmp_path):
    export_posts(tmp_path,[{"tweets":["approved"],"image":"images\\slot.png"}])
    with (tmp_path/"posts.csv").open(newline="",encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))==[{"Post Text":"approved","Image":"images/slot.png"}]


@pytest.mark.parametrize("caller",["type","agent","prompt","client"])
def test_custom_writer_cannot_search_through_any_config_caller(tmp_path,caller):
    shutil.copytree(ROOT/"post_types",tmp_path/"post_types")
    shutil.copytree(ROOT/"agents",tmp_path/"agents")
    shutil.copytree(ROOT/"skills",tmp_path/"skills")
    shutil.copytree(ROOT/"inputs",tmp_path/"inputs")
    shutil.copytree(tmp_path/"agents/thread_writer",tmp_path/"agents/custom_writer")
    agent_file=tmp_path/"agents/custom_writer/agent.yaml"
    data=yaml.safe_load(agent_file.read_text())
    data["skills"]=["brand_rules","web_search"]
    agent_file.write_text(yaml.safe_dump(data))
    type_file=tmp_path/"post_types/tech-thread/type.yaml"
    type_file.write_text(type_file.read_text().replace("writer: thread_writer","writer: custom_writer"))
    with pytest.raises(ValueError,match="writers cannot search"):
        if caller=="type": load_type(tmp_path,"tech-thread")
        elif caller=="agent": load_agent(tmp_path,"custom_writer")
        elif caller=="prompt": load_prompt(tmp_path,"custom_writer","tech-thread")
        else: configured_client(tmp_path,"custom_writer",Ledger(tmp_path/"ledger.json",100000,10),tmp_path/"usage.jsonl",prices=FixedPrices())


def test_custom_writer_brand_only_positive_control(tmp_path):
    shutil.copytree(ROOT/"post_types",tmp_path/"post_types")
    shutil.copytree(ROOT/"agents",tmp_path/"agents")
    shutil.copytree(tmp_path/"agents/thread_writer",tmp_path/"agents/custom_writer")
    type_file=tmp_path/"post_types/tech-thread/type.yaml"
    type_file.write_text(type_file.read_text().replace("writer: thread_writer","writer: custom_writer"))
    assert load_type(tmp_path,"tech-thread").writer=="custom_writer"
    assert load_agent(tmp_path,"custom_writer").skills==["brand_rules"]
