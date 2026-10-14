import json
import pytest
from google.genai import types
from google.genai.models import AsyncModels
from content_pipeline.ledger import Ledger
from content_pipeline.model_client import LangChainTransport, ModelClient, Prices
from content_pipeline.models import Draft


@pytest.mark.parametrize("name,effort,budget,level",[
    ("gemini-2.5-flash","none",0,None),
    ("gemini-2.5-flash","low",256,None),
    ("gemini-2.5-flash","medium",750,None),
    ("gemini-2.5-pro","none",128,None),
    ("gemini-3-flash-preview","medium",None,"MEDIUM"),
])
async def test_actual_gemini_sdk_request_reasoning_and_raw_usage(tmp_path,monkeypatch,name,effort,budget,level):
    monkeypatch.setenv("GEMINI_API_KEY","synthetic-unused")
    captured=[]
    async def generate(self,**kwargs):
        captured.append(kwargs)
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model",parts=[types.Part(text=json.dumps({"slot_id":"a","attempt":1,"tweets":[{"text":"Supported text.","source_ids":["KB-01"]}]}))]),finish_reason=types.FinishReason.STOP)],usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=11,candidates_token_count=20,thoughts_token_count=7,total_token_count=38))
    monkeypatch.setattr(AsyncModels,"generate_content",generate)
    ledger=Ledger(tmp_path/"ledger.json",10000,1)
    client=ModelClient("google_genai:"+name,ledger,tmp_path/"usage.jsonl",transport=LangChainTransport("google_genai:"+name,effort,tmp_path),prices=Prices(update=False))
    result=await client.call(Draft,[{"role":"user","content":"Return the requested draft"}],"a","writer",1,3000)
    assert result.tweets[0].text=="Supported text."
    config=captured[0]["config"]
    assert config.max_output_tokens==3000
    assert config.thinking_config.thinking_budget==budget
    actual_level=config.thinking_config.thinking_level
    assert (actual_level.value if actual_level is not None else None)==level
    assert config.response_mime_type=="application/json"
    assert config.response_json_schema is not None
    usage=json.loads((tmp_path/"usage.jsonl").read_text())
    assert (usage["input_tokens"],usage["output_tokens"],usage["reasoning_tokens"])==(11,27,7)
    assert ledger.totals()["tokens"]==38
