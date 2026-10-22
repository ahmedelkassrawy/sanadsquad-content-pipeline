"""Reviewer sends explicit SDK tool keys and repairs invalid SDK output once."""

import json
from pathlib import Path
import httpx
import pytest
from langchain_openai import ChatOpenAI
from content_pipeline.agents import Reviewer, load_rubric
from content_pipeline.config import load_type
from content_pipeline.contracts import ReviewRequest
from content_pipeline.ledger import Ledger
from content_pipeline.model_client import LangChainTransport, ModelClient, ModelFailure
from content_pipeline.models import Draft, ResearchPack, Tweet
from content_pipeline.research import KnowledgeBase
from test_regressions_sdk_validator_image_export import FixedPrices

ROOT = Path(__file__).resolve().parents[1]


def review_request(custom=False):
    spec = load_type(ROOT, "marketing-post")
    rubric = load_rubric(ROOT, spec)
    if custom:
        rubric = rubric.model_copy(
            update={
                "criteria": [
                    "technical accuracy",
                    "grounding-check",
                    "model_dump",
                    "__private__",
                    "1st_hook",
                ]
            }
        )
        spec = spec.model_copy(deep=True)
        spec.review.hard_fail = ["unsupported_claim", "model_dump", "brand-safe?"]
    source = KnowledgeBase(ROOT / "inputs/kb.json").source("SS-01")
    return ReviewRequest(
        slot_id="a",
        attempt=1,
        draft=Draft(
            slot_id="a",
            tweets=[
                Tweet(
                    text="External calls can reenter before state is finalized.",
                    source_ids=["SS-01"],
                )
            ],
        ),
        research=ResearchPack(
            slot_id="a", topic_id="SS-01", topic_name=source.title, sources=[source]
        ),
        post_type=spec,
        rubric=rubric,
    )


def sdk_client(tmp_path, monkeypatch, request, mode):
    credentials = tmp_path / "empty-credentials"
    credentials.mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-gateway-only")
    captures = []

    def handler(http_request):
        payload = json.loads(http_request.content)
        captures.append(payload)
        parameters = payload["tools"][0]["function"]["parameters"]
        properties = parameters["properties"]
        # A tool-capable producer needs explicit keys; an open object yields {}.
        explicit = set(properties["scores"].get("properties", {})) == set(request.rubric.criteria)
        args = {
            "slot_id": "a",
            "attempt": 1,
            "scores": {key: 4 for key in request.rubric.criteria},
            "hard_fail": {key: False for key in request.post_type.review.hard_fail},
            "reasons": {
                key: "Supported by the supplied evidence." for key in request.rubric.criteria
            },
        }
        if not explicit or mode == "empty":
            args.update(scores={}, hard_fail={}, reasons={})
        elif mode == "unsupported":
            args["hard_fail"]["unsupported_claim"] = True
            args["reasons"]["unsupported_claim"] = (
                "This claim is absent from the supplied evidence."
            )
        elif mode == "extra-string-reason":
            args["reasons"]["supporting note"] = "An extra explanation is allowed."
        elif mode == "missing-true-reason":
            args["hard_fail"]["unsupported_claim"] = True
        elif mode == "typo":
            args["scores"]["clearity"] = args["scores"].pop("clarity")
        elif mode == "range":
            args["scores"][request.rubric.criteria[0]] = 6
        elif mode == "approval":
            args["accepted"] = True
        elif mode == "null-false-reason":
            args["reasons"]["off_brand"] = None
        elif mode == "empty-false-reason":
            args["reasons"]["off_brand"] = ""
        elif mode == "empty-true-reason":
            args["hard_fail"]["unsupported_claim"] = True
            args["reasons"]["unsupported_claim"] = ""
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_wire",
                    "type": "function",
                    "function": {
                        "name": payload["tools"][0]["function"]["name"],
                        "arguments": json.dumps(args),
                    },
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-wire",
                "object": "chat.completion",
                "created": 1,
                "model": payload["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
            },
        )

    original = ChatOpenAI.__init__

    def initialize(chat, *args, **kwargs):
        kwargs["http_async_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        original(chat, *args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "__init__", initialize)
    model = "openrouter:dots-studio/dots-3-note-preview:free"
    client = ModelClient(
        model,
        Ledger(tmp_path / "ledger.json", 100000, 1),
        tmp_path / "usage.jsonl",
        transport=LangChainTransport(model, root=credentials),
        prices=FixedPrices(),
    )
    return client, captures


@pytest.mark.parametrize(
    "custom,mode",
    [
        (False, "supported"),
        (False, "unsupported"),
        (False, "extra-string-reason"),
        (True, "supported"),
    ],
)
async def test_public_reviewer_sends_explicit_sdk_tool_keys_and_preserves_canonical_semantics(
    tmp_path, monkeypatch, custom, mode
):
    request = review_request(custom)
    client, captures = sdk_client(tmp_path, monkeypatch, request, mode)
    result = await Reviewer(ROOT, "marketing-post", client)(request)
    assert result.accepted is (mode != "unsupported")
    assert result.scores == {key: 4.0 for key in request.rubric.criteria}
    assert result.hard_fail == (["unsupported_claim"] if mode == "unsupported" else [])
    assert len(captures) == 1
    properties = captures[0]["tools"][0]["function"]["parameters"]["properties"]
    for name, keys in [
        ("scores", request.rubric.criteria),
        ("hard_fail", request.post_type.review.hard_fail),
    ]:
        assert set(properties[name]["properties"]) == set(keys)
        assert set(properties[name]["required"]) == set(keys)
        assert properties[name]["additionalProperties"] is False
    for field in properties["scores"]["properties"].values():
        assert (field["minimum"], field["maximum"]) == (1, 5)
    reasons = properties["reasons"]
    assert set(reasons["required"]) == set(request.rubric.criteria)
    assert set(reasons["properties"]) == set(request.rubric.criteria) | set(
        request.post_type.review.hard_fail
    )
    assert reasons["additionalProperties"] == {"type": "string"}
    assert client.ledger.totals()["tokens"] == 50
    assert (tmp_path / "empty-credentials").exists() and list(
        (tmp_path / "empty-credentials").iterdir()
    ) == []


@pytest.mark.parametrize(
    "mode", ["empty", "missing-true-reason", "typo", "range", "approval", "null-false-reason"]
)
async def test_public_reviewer_repairs_once_then_rejects_invalid_sdk_output(
    tmp_path, monkeypatch, mode
):
    request = review_request()
    client, captures = sdk_client(tmp_path, monkeypatch, request, mode)
    with pytest.raises(ModelFailure, match="failed_output"):
        await Reviewer(ROOT, "marketing-post", client)(request)
    assert len(captures) == 2
    assert client.ledger.totals()["tokens"] == 100
    usage = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert [item["call_id"] for item in usage] == ["a/reviewer/1/1", "a/reviewer/1/2"]
    assert [item["status"] for item in usage] == ["confirmed", "confirmed"]
