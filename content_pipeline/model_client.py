import asyncio
import json
import math
import re
from contextlib import nullcontext
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from pydantic import BaseModel
from . import record_ids
from .ledger import Ledger
from .models import CallFailureKind, CallFinishReason, UsageRecord


OPENAI_COMPATIBLE_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "opencode": "https://opencode.ai/zen/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}


def valid_provider(name: str) -> bool:
    return re.fullmatch(r"[a-z][a-z0-9_]*", name) is not None


def _valid_token_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _reported_count(usage, provider_usage, usage_key, provider_key, fallback):
    value = usage.get(usage_key)

    if _valid_token_count(value):
        return value, False

    value = provider_usage.get(provider_key)

    if _valid_token_count(value):
        return value, False

    return fallback, True


def _safe_http_status(*values):
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value

    return None


def _retry_delay(error, status, transient):
    delay = 2 ** (transient - 1)
    fallback = 5 * delay if status == 429 else delay
    headers = getattr(getattr(error, "response", None), "headers", None) or {}

    try:
        retry_after = float(headers.get("Retry-After", headers.get("retry-after")))
    except (TypeError, ValueError):
        return fallback

    if not math.isfinite(retry_after) or retry_after < 0:
        return fallback

    return min(retry_after, 60)


def _safe_finish_reason(value):
    if isinstance(value, str):
        normalized = value.lower()

        if normalized in {
            reason.value for reason in CallFinishReason if reason != CallFinishReason.unknown
        }:
            return normalized

    return "unknown" if value is not None else None


def _failure_for_status(status):
    if status == 408:
        return CallFailureKind.timeout.value

    if status == 429:
        return CallFailureKind.rate_limit.value

    if status in {401, 403}:
        return CallFailureKind.auth.value

    if status == 402:
        return CallFailureKind.payment.value

    if status is not None and 500 <= status <= 599:
        return CallFailureKind.server.value

    return None


def _failure_for_exception(error, status):
    known = _failure_for_status(status)

    if known:
        return known

    try:
        from langchain_google_genai.chat_models import GoogleRateLimitError

        if isinstance(error, GoogleRateLimitError):
            return CallFailureKind.rate_limit.value
    except ImportError:
        pass

    try:
        import httpx

        from openai import APIConnectionError

        from openai import APITimeoutError

        from anthropic import APIConnectionError as AnthropicConnectionError

        from anthropic import APITimeoutError as AnthropicTimeoutError

        if isinstance(
            error, (TimeoutError, httpx.TimeoutException, APITimeoutError, AnthropicTimeoutError)
        ):
            return CallFailureKind.timeout.value

        if isinstance(
            error,
            (ConnectionError, httpx.ConnectError, APIConnectionError, AnthropicConnectionError),
        ):
            return CallFailureKind.connection.value
    except ImportError:
        if isinstance(error, TimeoutError):
            return CallFailureKind.timeout.value

        if isinstance(error, ConnectionError):
            return CallFailureKind.connection.value

    return CallFailureKind.unknown.value


def _payload_text(value):
    if value is None or value == [] or value == {}:
        return ""

    if isinstance(value, str):
        return value

    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        return str(value)


def _payload_identity(value):
    try:
        decoded = json.loads(value) if isinstance(value, str) else value

        return json.dumps(
            decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        return value


def _visible_output(raw, parsed):
    """Estimate returned payload size while reconciling mirrored SDK views."""
    parts = []
    seen = set()
    call_views = []
    content_call_counts = {}
    content = getattr(raw, "content", "")
    content_calls = []

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in {
                "tool_call",
                "server_tool_call",
                "tool_use",
            }:
                content_calls.append(block)

        add_content = content
    else:
        add_content = content

        if isinstance(content, str) and content.strip():
            try:
                decoded_content = json.loads(content)

                if isinstance(decoded_content, (dict, list)):
                    identity = _payload_identity(decoded_content)

                    content_call_counts[identity] = 1
            except (TypeError, ValueError):
                pass

    def add(value):
        text = _payload_text(value)

        if text.strip():
            identity = _payload_identity(text)

            if identity not in seen:
                seen.add(identity)

                parts.append(text)

    add(add_content)

    normalized_calls = [
        *(getattr(raw, "tool_calls", None) or []),
        *(getattr(raw, "invalid_tool_calls", None) or []),
    ]

    if normalized_calls:
        call_views.append(normalized_calls)

    additional = getattr(raw, "additional_kwargs", None) or {}

    if isinstance(additional, dict) and additional.get("tool_calls"):
        call_views.append(list(additional["tool_calls"]))

    if content_calls:
        call_views.append(content_calls)

    def arguments_of(call):
        if isinstance(call, dict):
            function = call.get("function") or {}
            arguments = call.get("args")

            if arguments is None and call.get("type") == "tool_use":
                arguments = call.get("input")

            if arguments is None:
                arguments = function.get("arguments")
        else:
            function = getattr(call, "function", None)
            arguments = getattr(call, "args", None)

            if arguments is None and function is not None:
                arguments = getattr(function, "arguments", None)

        return arguments

    # Each SDK field is a view of the response. Count equal payload occurrences
    # by the maximum multiplicity in any one view, not their sum across views.
    call_multiplicity = {}
    call_ids = {}
    call_text = {}

    for view in call_views:
        counts = {}
        ids_in_view = {}

        for call in view:
            arguments = arguments_of(call)
            text = _payload_text(arguments)

            if arguments is None or not text.strip():
                continue

            identity = _payload_identity(text)
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)

            if call_id is None:
                counts[identity] = counts.get(identity, 0) + 1
            else:
                ids_in_view.setdefault(identity, set()).add(str(call_id))

                call_ids.setdefault(identity, set()).add(str(call_id))

            call_text[identity] = text

        for identity, ids in ids_in_view.items():
            counts[identity] = counts.get(identity, 0) + len(ids)

        for identity, count in counts.items():
            call_multiplicity[identity] = max(call_multiplicity.get(identity, 0), count)

    for identity, count in call_multiplicity.items():
        total = max(count, len(call_ids.get(identity, ())))
        content_count = content_call_counts.get(identity, 0)

        if content_calls:
            content_ids = set()
            content_idless = 0

            for call in content_calls:
                arguments = arguments_of(call)

                if (
                    arguments is not None
                    and _payload_identity(_payload_text(arguments)) == identity
                ):
                    call_id = (
                        call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
                    )

                    if call_id is None:
                        content_idless += 1
                    else:
                        content_ids.add(str(call_id))

            content_count = content_idless + len(content_ids)

        extra_count = max(0, total - content_count)

        parts.extend([call_text[identity]] * extra_count)

    function_call = getattr(raw, "function_call", None)

    if function_call is None and isinstance(additional, dict):
        function_call = additional.get("function_call")

    if function_call is not None:
        arguments = (
            function_call.get("arguments")
            if isinstance(function_call, dict)
            else getattr(function_call, "arguments", None)
        )

        add(arguments if arguments is not None else function_call)

    if not parts and parsed is not None:
        add(parsed.model_dump() if isinstance(parsed, BaseModel) else parsed)

    return "".join(parts)


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

        price = calc_price(
            Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cached,
                cache_write_tokens=cache_creation,
            ),
            name,
            provider_id=provider,
        )

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

        key_name = {
            "openrouter": "OPENROUTER_API_KEY",
            "opencode": "OPENCODE_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google_genai": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }.get(provider)

        if not key_name:
            if not valid_provider(provider):
                raise ValueError("unsupported configured model provider")

            key_name = provider.upper() + "_API_KEY"

        key = env.get(key_name)

        if not key or not key.strip():
            raise ValueError("missing credential for selected provider")

        options = {"max_tokens": max_tokens, "max_retries": 0, "api_key": key}

        if self.reasoning_effort != "none":
            options["reasoning_effort"] = self.reasoning_effort
        elif provider == "openrouter":
            options["extra_body"] = {"reasoning": {"enabled": False}}

        if provider in OPENAI_COMPATIBLE_BASE_URLS:
            options["base_url"] = OPENAI_COMPATIBLE_BASE_URLS[provider]
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
                    target = {"low": 256, "medium": 768, "high": 1536}[self.reasoning_effort]
                    budget = max(minimum, min(target, max_tokens // 4))

                if budget >= max_tokens:
                    raise ValueError("output cap too small for configured thinking")

                options["thinking_budget"] = budget
            elif name.startswith(("gemini-3", "gemini-4")):
                options["reasoning_effort"] = (
                    self.reasoning_effort
                    if self.reasoning_effort != "none"
                    else "minimal"
                    if "flash" in name
                    else "low"
                )

        chat = init_chat_model(name, model_provider=provider, **options)

        # Tool structured output retains raw usage before LangChain parses it;
        # native SDK parsing can raise on length or bad JSON before returning usage.
        method = {
            "openai": "function_calling",
            "google_genai": "json_schema",
            "anthropic": "function_calling",
        }.get(provider, "function_calling")

        return chat.with_structured_output(schema, method=method, include_raw=True)

    async def __call__(self, messages, schema, max_tokens):
        return await self.prepare(schema, max_tokens).ainvoke(messages)


def configured_client(
    root: Path,
    agent_name: str,
    ledger: Ledger,
    usage_path: Path,
    settings=None,
    credential_root: Path | None = None,
    **kwargs,
):
    from .config import Settings, load_agent, resolve_model

    settings = settings or Settings.load(root)
    agent = load_agent(root, agent_name)
    model = resolve_model(settings, agent)

    return ModelClient(
        model,
        ledger,
        usage_path,
        transport=LangChainTransport(
            model,
            settings.reasoning_effort_override or agent.reasoning_effort,
            credential_root or root,
        ),
        allow_unpriced=settings.allow_unpriced,
        repair_prompt=root / "config/repair.md",
        **kwargs,
    )


@dataclass
class _Attempt:
    row: UsageRecord
    parsed: BaseModel | None = None
    error: BaseException | None = None
    transport_error: bool = False
    repairable: bool = False


@dataclass
class _AttemptOutcome:
    accounting: str
    action: str
    failure: str | None = None
    delay: float = 0


def _exception_status(error):
    response = getattr(error, "response", None)
    status = _safe_http_status(
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(response, "status_code", None),
    )

    if status is None:
        from langchain_google_genai.chat_models import GoogleRateLimitError

        if isinstance(error, GoogleRateLimitError):
            status = _safe_http_status(getattr(error.__cause__, "code", None))

    return status


def _decide_attempt(attempt, transient, truncation, repair):
    row = attempt.row

    if attempt.transport_error:
        error = attempt.error
        status = row.http_status
        cancelled = isinstance(error, asyncio.CancelledError)
        definite = status is not None and 400 <= status < 500 and status != 408
        accounting = "release" if definite and not cancelled else "uncertain"

        if cancelled:
            return _AttemptOutcome(accounting, "raise")

        if status in {401, 402, 403}:
            failure = "failed_budget" if status == 402 else "failed_api"
            return _AttemptOutcome(accounting, "fail", failure)

        from langchain_google_genai.chat_models import GoogleRateLimitError
        from openai import APIConnectionError
        from anthropic import APIConnectionError as AnthropicConnectionError

        retryable = isinstance(
            error,
            (
                TimeoutError,
                ConnectionError,
                APIConnectionError,
                AnthropicConnectionError,
                GoogleRateLimitError,
            ),
        ) or status in {408, 429, 500, 502, 503, 504}

        if retryable and transient < 3:
            retry_status = 429 if isinstance(error, GoogleRateLimitError) else status
            return _AttemptOutcome(
                accounting, "retry", delay=_retry_delay(error, retry_status, transient + 1)
            )

        return _AttemptOutcome(accounting, "fail", "failed_api")

    if row.status == "uncertain":
        failure = (
            "failed_output"
            if row.failure_kind == CallFailureKind.malformed_envelope
            else "failed_api"
        )
        return _AttemptOutcome("uncertain", "fail", failure)

    if attempt.error is not None:
        if attempt.repairable:
            return (
                _AttemptOutcome("settle", "fail", "failed_output")
                if repair
                else (_AttemptOutcome("settle", "repair"))
            )

        return _AttemptOutcome("settle", "raise")

    if row.failure_kind == CallFailureKind.provider_error:
        return (
            _AttemptOutcome("settle", "retry", delay=2**transient)
            if transient < 3
            else _AttemptOutcome("settle", "fail", "failed_api")
        )

    if row.failure_kind == CallFailureKind.truncated:
        return (
            _AttemptOutcome("settle", "fail", "failed_output")
            if truncation
            else _AttemptOutcome("settle", "expand")
        )

    return _AttemptOutcome("settle", "return")


class ModelClient:
    def __init__(
        self,
        model: str,
        ledger: Ledger,
        usage_path: Path,
        transport=None,
        prices=None,
        allow_unpriced=False,
        repair_prompt: Path | None = None,
        sleep=asyncio.sleep,
        usage_lock=None,
        limiter=None,
    ):
        self.model = model
        self.ledger = ledger
        self.usage_path = usage_path
        self.transport = transport or LangChainTransport(model)
        self.prices = prices or Prices()
        self.allow_unpriced = allow_unpriced
        self.repair_prompt = (
            repair_prompt or Path(__file__).resolve().parents[1] / "config" / "repair.md"
        )
        self.sleep = sleep
        self.usage_lock = usage_lock or asyncio.Lock()
        self.limiter = limiter

    def quote(self, inputs, outputs, cached=0, cache_creation=0):
        try:
            return self.prices.quote(self.model, inputs, outputs, cached, cache_creation)
        except LookupError:
            if not self.allow_unpriced:
                raise ModelFailure("unpriced_model") from None

            return None, "unknown"

    async def record(self, record: UsageRecord):
        parsed = record_ids.parse_record_id(record.call_id)

        record = record.model_copy(
            update={"slot_id": parsed.slot_id, "node": parsed.node, "attempt": parsed.attempt}
        )

        async with self.usage_lock:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)

            with self.usage_path.open("a", encoding="utf-8") as stream:
                stream.write(record.model_dump_json() + "\n")

    def _uncertain_usage(
        self,
        call_id,
        inputs,
        outputs,
        cost,
        cost_source,
        failure,
        http_status=None,
        estimated=True,
    ):
        return UsageRecord(
            call_id=call_id,
            model=self.model,
            input_tokens=inputs,
            output_tokens=outputs,
            estimated=estimated,
            cost_usd=cost,
            cost_source=cost_source,
            status="uncertain",
            failure_kind=failure,
            http_status=http_status,
        )

    def _response_usage(self, result, call_id, inputs, reserve_tokens, cost, cost_source):
        raw = result["raw"]
        usage = getattr(raw, "usage_metadata", None) or {}
        metadata = getattr(raw, "response_metadata", None) or {}
        provider_usage = metadata.get("token_usage") or metadata.get("usage") or {}

        if not isinstance(usage, dict):
            usage = {}

        if not isinstance(provider_usage, dict):
            provider_usage = {}

        output_text = _visible_output(raw, result.get("parsed"))

        in_tokens, estimated_input = _reported_count(
            usage, provider_usage, "input_tokens", "prompt_tokens", inputs
        )

        out_tokens, estimated_output = _reported_count(
            usage,
            provider_usage,
            "output_tokens",
            "completion_tokens",
            math.ceil(len(output_text) / 3.5),
        )

        estimated = estimated_input or estimated_output
        details = usage.get("input_token_details") or {}

        if not isinstance(details, dict):
            details = {}

        output_details = usage.get("output_token_details") or {}

        if not isinstance(output_details, dict):
            output_details = {}

        cached = details.get("cache_read", 0)
        creation = details.get("cache_creation", 0)
        reasoning = output_details.get("reasoning", 0)
        cached = cached if _valid_token_count(cached) else 0
        creation = creation if _valid_token_count(creation) else 0
        reasoning = reasoning if _valid_token_count(reasoning) else 0

        if provider_usage.get("cost") is not None:
            cost = float(provider_usage["cost"]) + float(
                (provider_usage.get("cost_details") or {}).get("upstream_inference_cost") or 0
            )

            cost_source = "provider"
        else:
            try:
                cost, cost_source = self.quote(in_tokens, out_tokens, cached, creation)
            except Exception:
                return self._uncertain_usage(
                    call_id,
                    in_tokens,
                    out_tokens,
                    cost,
                    cost_source,
                    CallFailureKind.unknown.value,
                    estimated=estimated,
                )

        raw_status = _safe_http_status(
            metadata.get("http_status"), metadata.get("status_code"), metadata.get("status")
        )

        finish_value = metadata.get("finish_reason") or metadata.get("stop_reason")
        finish = _safe_finish_reason(finish_value)

        return UsageRecord(
            call_id=call_id,
            model=self.model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
            estimated=estimated,
            cost_usd=cost,
            cost_source=cost_source,
            overshoot_tokens=max(0, in_tokens + out_tokens - reserve_tokens),
            http_status=raw_status,
            finish_reason=finish,
        )

    async def _run_attempt(
        self,
        prepared,
        schema,
        messages,
        max_output_tokens,
        call_id,
        inputs,
        reserve_tokens,
        cost,
        cost_source,
        output_validator,
        retries,
    ):
        try:
            async with self.limiter if self.limiter is not None else nullcontext():
                result = (
                    await prepared.ainvoke(messages)
                    if prepared is not None
                    else await self.transport(messages, schema, max_output_tokens)
                )
        except BaseException as error:
            status = _exception_status(error)
            row = self._uncertain_usage(
                call_id,
                inputs,
                max_output_tokens,
                cost,
                cost_source,
                _failure_for_exception(error, status),
                status,
            )
            completed = await self._finish_attempt(
                _Attempt(row, error=error, transport_error=True), retries
            )

            if completed[0].action == "raise":
                raise

            return completed

        if not isinstance(result, dict) or "raw" not in result:
            return await self._finish_attempt(
                _Attempt(
                    self._uncertain_usage(
                        call_id,
                        inputs,
                        max_output_tokens,
                        cost,
                        cost_source,
                        CallFailureKind.malformed_envelope.value,
                    )
                ),
                retries,
            )

        row = self._response_usage(result, call_id, inputs, reserve_tokens, cost, cost_source)

        if row.status == "uncertain":
            return await self._finish_attempt(_Attempt(row), retries)

        metadata = getattr(result["raw"], "response_metadata", None) or {}
        finish_value = metadata.get("finish_reason") or metadata.get("stop_reason")

        try:
            route_finish = str(finish_value or "").lower()
        except BaseException as error:
            await self._finish_attempt(_Attempt(row, error=error), retries)

            raise

        if route_finish in {"error", "length", "max_tokens"}:
            failure = (
                CallFailureKind.provider_error
                if route_finish == "error"
                else CallFailureKind.truncated
            )
            return await self._finish_attempt(
                _Attempt(row.model_copy(update={"failure_kind": failure})), retries
            )

        failure = CallFailureKind.malformed_output

        try:
            if result.get("parsing_error") or result.get("parsed") is None:
                raise ValueError("malformed structured response")

            parsed = schema.model_validate(result["parsed"])
            failure = CallFailureKind.output_rejected

            if output_validator is not None:
                output_validator(parsed)
        except (ValueError, TypeError) as error:
            return await self._finish_attempt(
                _Attempt(
                    row.model_copy(update={"failure_kind": failure}), error=error, repairable=True
                ),
                retries,
            )
        except BaseException as error:
            if failure == CallFailureKind.output_rejected:
                row = row.model_copy(update={"failure_kind": failure})

            await self._finish_attempt(_Attempt(row, error=error), retries)

            raise

        return await self._finish_attempt(_Attempt(row, parsed=parsed), retries)

    async def _finish_attempt(self, result, retries):
        outcome = _decide_attempt(result, *retries)
        row = result.row
        call_id = row.call_id

        if outcome.accounting == "release":
            await self.ledger.release(call_id)
            row = row.model_copy(
                update={
                    "status": "confirmed",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0,
                    "estimated": False,
                }
            )
        elif outcome.accounting == "uncertain":
            await self.ledger.uncertain(call_id)
        else:
            await self.ledger.settle(call_id, row.input_tokens + row.output_tokens, row.cost_usd)

        await self.record(row)

        if outcome.action == "fail":
            raise ModelFailure(outcome.failure) from None

        return outcome, result

    def _repair_messages(self, messages, error):
        # Only locations/types enter repair feedback, never model input or credentials.
        errors = (
            error.errors(include_input=False, include_context=False)
            if hasattr(error, "errors")
            else [{"type": "malformed_structured_response"}]
        )
        safe_error = json.dumps(
            [{"loc": item.get("loc", []), "type": item["type"]} for item in errors]
        )

        return [
            *messages,
            {
                "role": "user",
                "content": self.repair_prompt.read_text(encoding="utf-8").replace(
                    "{parse_error}", safe_error
                ),
            },
        ]

    async def call(
        self,
        schema: type[BaseModel],
        messages: list[dict],
        slot_id: str,
        node: str,
        attempt: int,
        max_output_tokens: int,
        output_validator=None,
    ):
        repair = truncation = transient = 0
        messages = list(messages)
        call_n = record_ids.next_call_n(self.ledger.entries, slot_id, node, attempt)

        while True:
            call_id = record_ids.call_id(slot_id, node, attempt, call_n)
            inputs = math.ceil(sum(len(str(m["content"])) for m in messages) / 3.5 * 1.15)
            reserve_tokens = inputs + max_output_tokens

            cost, cost_source = self.quote(inputs, max_output_tokens)

            try:
                prepared = (
                    self.transport.prepare(schema, max_output_tokens)
                    if hasattr(self.transport, "prepare")
                    else None
                )
            except Exception:
                raise ModelFailure("failed_api") from None

            await self.ledger.reserve(call_id, reserve_tokens, cost)
            await self.ledger.dispatch(call_id)

            outcome, result = await self._run_attempt(
                prepared,
                schema,
                messages,
                max_output_tokens,
                call_id,
                inputs,
                reserve_tokens,
                cost,
                cost_source,
                output_validator,
                (transient, truncation, repair),
            )

            if outcome.action == "retry":
                transient += 1
                await self.sleep(outcome.delay)
            elif outcome.action == "expand":
                truncation += 1
                max_output_tokens = math.ceil(max_output_tokens * 1.5)
            elif outcome.action == "repair":
                repair += 1
                messages = self._repair_messages(messages, result.error)
            else:
                return result.parsed

            call_n += 1
