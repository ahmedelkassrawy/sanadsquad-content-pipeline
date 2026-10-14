import asyncio
import json
import math
from importlib.metadata import version
from pathlib import Path
from pydantic import BaseModel
from .ledger import Ledger
from .models import UsageRecord


class ModelFailure(Exception):
    def __init__(self, outcome):
        super().__init__(outcome)
        self.outcome = outcome


class Prices:
    def __init__(self, update=True):
        self.updater = None
        if update:
            from genai_prices import UpdatePrices
            self.updater = UpdatePrices()
            self.updater.__enter__()

    def close(self):
        if self.updater:
            self.updater.__exit__(None, None, None)

    def quote(self, model, input_tokens, output_tokens, cached=0, cache_creation=0):
        from genai_prices import Usage, calc_price
        provider, name = model.split(":", 1)
        if provider == "openrouter" or (provider == "opencode" and "/" in name):
            provider, name = name.split("/", 1)
        elif provider == "opencode":
            provider = None
        price = calc_price(Usage(input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=cached, cache_write_tokens=cache_creation), name, provider_id=provider)
        return float(price.total_price), "genai-prices@" + version("genai-prices")


class LangChainTransport:
    def __init__(self, model: str, reasoning_effort: str = "none", root: Path | None = None):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.root = root or Path(__file__).resolve().parents[1]

    def prepare(self, schema, max_tokens):
        from langchain.chat_models import init_chat_model
        import os
        from dotenv import dotenv_values
        env = {**dotenv_values(self.root / ".env"), **os.environ}
        provider, name = self.model.split(":", 1)
        key_name = {"openrouter":"OPENROUTER_API_KEY","opencode":"OPENCODE_API_KEY","openai":"OPENAI_API_KEY","google_genai":"GEMINI_API_KEY","anthropic":"ANTHROPIC_API_KEY"}.get(provider)
        if not key_name:
            raise ValueError("unsupported configured model provider")
        key = env.get(key_name)
        if not key or not key.strip():
            raise ValueError("missing credential for selected provider")
        options = {"max_tokens": max_tokens, "max_retries": 0, "api_key": key}
        if self.reasoning_effort != "none":
            options["reasoning_effort"] = self.reasoning_effort
        if provider in {"openrouter", "opencode"}:
            options["base_url"] = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://opencode.ai/zen/v1"
            provider = "openai"
        if provider == "google_genai":
            options.pop("max_tokens")
            options["max_output_tokens"] = max_tokens
            options.pop("reasoning_effort", None)
            if name.startswith("gemini-2.5-"):
                minimum = 128 if "pro" in name else 512 if "lite" in name else 0
                if self.reasoning_effort == "none":
                    budget = 128 if "pro" in name else 0
                else:
                    target = {"low":256,"medium":768,"high":1536}[self.reasoning_effort]
                    budget = max(minimum, min(target, max_tokens // 4))
                if budget >= max_tokens:
                    raise ValueError("output cap too small for configured thinking")
                options["thinking_budget"] = budget
            elif name.startswith(("gemini-3", "gemini-4")):
                options["reasoning_effort"] = self.reasoning_effort if self.reasoning_effort != "none" else "minimal" if "flash" in name else "low"
        chat = init_chat_model(name, model_provider=provider, **options)
        # Tool structured output retains raw usage before LangChain parses it;
        # native SDK parsing can raise on length or bad JSON before returning usage.
        method = {"openai":"function_calling","google_genai":"json_schema","anthropic":"function_calling"}[provider]
        return chat.with_structured_output(schema, method=method, include_raw=True)

    async def __call__(self, messages, schema, max_tokens):
        return await self.prepare(schema,max_tokens).ainvoke(messages)


def configured_client(root: Path, agent_name: str, ledger: Ledger, usage_path: Path, settings=None, credential_root: Path | None = None, **kwargs):
    from .config import Settings, load_agent, resolve_model
    settings = settings or Settings.load(root)
    agent = load_agent(root,agent_name)
    model = resolve_model(settings,agent)
    return ModelClient(model,ledger,usage_path,transport=LangChainTransport(model,agent.reasoning_effort,credential_root or root),allow_unpriced=settings.allow_unpriced,repair_prompt=root/"config/repair.md",**kwargs)


class ModelClient:
    def __init__(self, model: str, ledger: Ledger, usage_path: Path, transport=None, prices=None, allow_unpriced=False, repair_prompt: Path | None = None, sleep=asyncio.sleep, usage_lock=None):
        self.model = model
        self.ledger = ledger
        self.usage_path = usage_path
        self.transport = transport or LangChainTransport(model)
        self.prices = prices or Prices()
        self.allow_unpriced = allow_unpriced
        self.repair_prompt = repair_prompt or Path(__file__).resolve().parents[1] / "config" / "repair.md"
        self.sleep = sleep
        self.usage_lock = usage_lock or asyncio.Lock()

    def quote(self, inputs, outputs, cached=0, cache_creation=0):
        try:
            return self.prices.quote(self.model, inputs, outputs, cached, cache_creation)
        except LookupError:
            if not self.allow_unpriced:
                raise ModelFailure("unpriced_model") from None
            return None, "unknown"

    async def record(self, record: UsageRecord):
        slot,node,attempt,_=record.call_id.rsplit("/",3)
        record=record.model_copy(update={"slot_id":slot,"node":node,"attempt":int(attempt)})
        async with self.usage_lock:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_path.open("a", encoding="utf-8") as stream:
                stream.write(record.model_dump_json() + "\n")

    async def call(self, schema: type[BaseModel], messages: list[dict], slot_id: str, node: str, attempt: int, max_output_tokens: int, output_validator=None):
        repair = truncation = transient = 0
        messages = list(messages)
        call_n = 0
        while True:
            call_n += 1
            call_id = f"{slot_id}/{node}/{attempt}/{call_n}"
            inputs = math.ceil(sum(len(str(m["content"])) for m in messages) / 3.5 * 1.15)
            reserve_tokens = inputs + max_output_tokens
            cost, cost_source = self.quote(inputs, max_output_tokens)
            try:
                prepared = self.transport.prepare(schema,max_output_tokens) if hasattr(self.transport,"prepare") else None
            except Exception:
                raise ModelFailure("failed_api") from None
            await self.ledger.reserve(call_id, reserve_tokens, cost)
            await self.ledger.dispatch(call_id)
            try:
                result = await prepared.ainvoke(messages) if prepared is not None else await self.transport(messages, schema, max_output_tokens)
            except BaseException as error:
                await self.ledger.uncertain(call_id)
                await self.record(UsageRecord(call_id=call_id, model=self.model, input_tokens=inputs, output_tokens=max_output_tokens, estimated=True, cost_usd=cost, cost_source=cost_source, status="uncertain"))
                if isinstance(error, asyncio.CancelledError):
                    raise
                status = getattr(error, "status_code", None) or getattr(error,"code",None)
                if status in {402, 403}:
                    raise ModelFailure("failed_budget") from None
                from openai import APIConnectionError
                from anthropic import APIConnectionError as AnthropicConnectionError
                retryable = isinstance(error, (TimeoutError, ConnectionError, APIConnectionError, AnthropicConnectionError)) or status in {408, 429, 500, 502, 503, 504}
                if retryable and transient < 3:
                    transient += 1
                    await self.sleep(2 ** (transient - 1))
                    continue
                raise ModelFailure("failed_api") from None
            if not isinstance(result, dict) or "raw" not in result:
                await self.ledger.uncertain(call_id)
                await self.record(UsageRecord(call_id=call_id,model=self.model,input_tokens=inputs,output_tokens=max_output_tokens,estimated=True,cost_usd=cost,cost_source=cost_source,status="uncertain"))
                raise ModelFailure("failed_output")
            raw = result["raw"]
            usage = getattr(raw, "usage_metadata", None) or {}
            metadata = getattr(raw, "response_metadata", None) or {}
            provider_usage = metadata.get("token_usage") or metadata.get("usage") or {}
            estimated = not bool(usage or provider_usage.get("prompt_tokens"))
            in_tokens = usage.get("input_tokens", provider_usage.get("prompt_tokens", inputs))
            out_tokens = usage.get("output_tokens", provider_usage.get("completion_tokens", math.ceil(len(str(getattr(raw, "content", ""))) / 3.5)))
            details = usage.get("input_token_details", {})
            cached = details.get("cache_read", 0)
            creation = details.get("cache_creation", 0)
            reasoning = usage.get("output_token_details", {}).get("reasoning", 0)
            if provider_usage.get("cost") is not None:
                cost = float(provider_usage["cost"]) + float((provider_usage.get("cost_details") or {}).get("upstream_inference_cost") or 0)
                cost_source = "provider"
            else:
                try:
                    cost, cost_source = self.quote(in_tokens, out_tokens, cached, creation)
                except Exception:
                    await self.ledger.uncertain(call_id)
                    await self.record(UsageRecord(call_id=call_id,model=self.model,input_tokens=in_tokens,output_tokens=out_tokens,cost_usd=cost,cost_source=cost_source,status="uncertain",estimated=estimated))
                    raise ModelFailure("failed_api") from None
            await self.ledger.settle(call_id, in_tokens + out_tokens, cost)
            await self.record(UsageRecord(call_id=call_id, model=self.model, input_tokens=in_tokens, output_tokens=out_tokens, cached_input_tokens=cached, reasoning_tokens=reasoning, estimated=estimated, cost_usd=cost, cost_source=cost_source, overshoot_tokens=max(0, in_tokens + out_tokens - reserve_tokens)))
            finish = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").lower()
            if finish in {"length", "max_tokens"}:
                if truncation:
                    raise ModelFailure("failed_output")
                truncation += 1
                max_output_tokens = math.ceil(max_output_tokens * 1.5)
                continue
            try:
                if result.get("parsing_error") or result.get("parsed") is None:
                    raise ValueError("malformed structured response")
                parsed=schema.model_validate(result["parsed"])
                if output_validator is not None:
                    output_validator(parsed)
                return parsed
            except (ValueError, TypeError) as parse_error:
                if repair:
                    raise ModelFailure("failed_output") from None
                repair += 1
                # Only locations/types enter repair feedback, never echoed model input or credentials.
                errors = parse_error.errors(include_input=False, include_context=False) if hasattr(parse_error,"errors") else [{"type":"malformed_structured_response"}]
                safe_error = json.dumps([{ "loc": error.get("loc", []), "type": error["type"]} for error in errors])
                messages = [*messages, {"role": "user", "content": self.repair_prompt.read_text(encoding="utf-8").replace("{parse_error}",safe_error)}]
