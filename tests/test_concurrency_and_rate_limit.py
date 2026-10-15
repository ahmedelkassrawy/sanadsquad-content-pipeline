"""Offline dispatch limits, backoff, configuration, and saved-policy compatibility."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from content_pipeline import cli, model_client
from content_pipeline.config import Settings
from content_pipeline.ledger import Ledger
from content_pipeline.model_client import ModelClient, ModelFailure
from content_pipeline.models import Draft
from content_pipeline.pipeline import RunContext, default_model_factory


ROOT = Path(__file__).resolve().parents[1]


class FixedPrices:
    def quote(self, *args):
        return .001, "offline-fixture"


def response(slot="a"):
    return {"raw": SimpleNamespace(content="Supported.", usage_metadata={"input_tokens": 2, "output_tokens": 3}, response_metadata={}),
            "parsed": {"slot_id": slot, "tweets": [{"text": "Supported.", "source_ids": ["KB-01"]}]}}


async def invoke(client, slot="a"):
    return await client.call(Draft, [{"role": "user", "content": "Write."}], slot, "writer", 1, 100)


@pytest.mark.parametrize("limit", [2, None])
@pytest.mark.parametrize("prepared", [False, True])
async def test_run_wide_concurrent_dispatches(tmp_path, monkeypatch, limit, prepared):
    active = peak = 0

    class Transport:
        async def __call__(self, *args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0)
                return response()
            finally:
                active -= 1

    class PreparedTransport(Transport):
        def prepare(self, *args):
            return SimpleNamespace(ainvoke=self.__call__)

    monkeypatch.setattr(model_client, "LangChainTransport", lambda *args: PreparedTransport() if prepared else Transport())
    # model_copy lets the base execute dispatches, proving the missing limit itself.
    settings = Settings().model_copy(update={"max_concurrency": limit})
    context = RunContext(ROOT, ROOT, tmp_path, settings, Ledger(tmp_path/"ledger.json", 100000, 10), FixedPrices(), asyncio.Lock())
    clients = [default_model_factory("reviewer", str(i), context) for i in range(6)]
    await asyncio.gather(*(invoke(client, str(i)) for i, client in enumerate(clients)))
    assert peak == 2 if limit == 2 else peak > 2


@pytest.mark.parametrize("status,header,expected", [(429, None, 5), (429, "12", 12), (429, "999", 60), (429, "bad", 5), (503, "12", 1)])
async def test_status_backoff(tmp_path, status, header, expected):
    waits = []
    calls = 0

    async def transport(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = RuntimeError("offline failure")
            error.status_code = status
            error.response = httpx.Response(status, headers={} if header is None else {"Retry-After": header})
            raise error
        return response()

    async def sleep(delay):
        waits.append(delay)

    client = ModelClient("openai:fixture", Ledger(tmp_path/"ledger.json", 100000, 10), tmp_path/"usage.jsonl", transport=transport, prices=FixedPrices(), sleep=sleep)
    assert (await invoke(client)).slot_id == "a"
    assert waits == [expected]
    assert calls == 2


@pytest.mark.parametrize("status,expected", [(429, [5, 10, 20]), (503, [1, 2, 4])])
async def test_retry_exhaustion(tmp_path, status, expected):
    waits = []

    async def transport(*args):
        error = RuntimeError("offline failure")
        error.status_code = status
        raise error

    async def sleep(delay):
        waits.append(delay)

    client = ModelClient("openai:fixture", Ledger(tmp_path/"ledger.json", 100000, 10), tmp_path/"usage.jsonl", transport=transport, prices=FixedPrices(), sleep=sleep)
    with pytest.raises(ModelFailure, match="failed_api"):
        await invoke(client)
    assert waits == expected
    rows = [json.loads(line) for line in (tmp_path/"usage.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert all(row["status"] == "uncertain" and row["http_status"] == status for row in rows)


def test_settings_environment_and_legacy_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MAX_CONCURRENCY", raising=False)
    assert Settings.load(tmp_path).max_concurrency is None
    assert Settings.model_validate_json('{"advisor_enabled":false}').max_concurrency is None
    monkeypatch.setenv("MAX_CONCURRENCY", "3")
    assert Settings.load(tmp_path).max_concurrency == 3
    assert Settings.load(tmp_path, {"max_concurrency": 2}).max_concurrency == 2
    with pytest.raises(ValueError):
        Settings(max_concurrency=0)


@pytest.mark.parametrize("piece", [False, True])
def test_cli_override(tmp_path, monkeypatch, piece):
    monkeypatch.setenv("MAX_CONCURRENCY", "3")
    seen = []
    monkeypatch.setattr(cli, "estimate", lambda root, slots, settings: seen.append(settings) or SimpleNamespace(model_dump_json=lambda **kwargs: "{}"))
    monkeypatch.setattr(cli, "load_plan", lambda *args: [])
    argv = ["--root", str(tmp_path), "--estimate", "--max-concurrency", "2"]
    if piece:
        argv += ["--type", "marketing-post", "--topic", "security"]
    assert cli.main(argv, piece=piece) == 0
    assert seen[0].max_concurrency == 2


async def test_backoff_releases_shared_limiter(tmp_path):
    limiter = asyncio.Semaphore(1)
    calls = 0
    waits = []

    async def transport(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = RuntimeError("offline rate limit")
            error.status_code = 429
            raise error
        return response()

    async def sleep(delay):
        assert not limiter.locked()
        waits.append(delay)
        await asyncio.wait_for(invoke(sibling, "sibling"), timeout=2)

    kwargs = dict(transport=transport, prices=FixedPrices(), limiter=limiter)
    ledger = Ledger(tmp_path/"ledger.json", 100000, 10)
    client = ModelClient("openai:fixture", ledger, tmp_path/"usage.jsonl", sleep=sleep, **kwargs)
    sibling = ModelClient("openai:fixture", ledger, tmp_path/"usage.jsonl", **kwargs)
    await invoke(client)
    assert calls == 3 and waits == [5]
    assert not limiter.locked()


@pytest.mark.parametrize("legacy", [False, True])
async def test_resume_preserves_saved_concurrency(tmp_path, monkeypatch, legacy):
    from content_pipeline.durability import snapshot_hashes
    from content_pipeline.models import ResolvedSlot
    from content_pipeline.pipeline import RunInterrupted, run_pipeline
    from test_day3_delivery import DeliveryModels, search_factory

    models = DeliveryModels()
    output = tmp_path/"run"
    with pytest.raises(RunInterrupted):
        await run_pipeline(ROOT, [ResolvedSlot(slot_id="a", type="tech-thread", focus="reentrancy")], output,
                           Settings(max_concurrency=2), model_factory=models.factory, search_factory=search_factory,
                           interrupt_after=["lane:write"])
    if legacy:
        for path in [output/"snapshot/resolved-config.json", output/"runtime-settings.json"]:
            saved = json.loads(path.read_text())
            saved.pop("max_concurrency")
            path.write_text(json.dumps(saved))
        identity_path = output/"run_identity.json"
        identity = json.loads(identity_path.read_text())
        identity["snapshot_files"] = snapshot_hashes(output/"snapshot")
        identity_path.write_text(json.dumps(identity))
    monkeypatch.setenv("MAX_CONCURRENCY", "7")
    seen = []

    def factory(name, slot, context):
        seen.append(context.settings.max_concurrency)
        return models.factory(name, slot, context)

    summary = await run_pipeline(ROOT, resume=output, model_factory=factory, search_factory=search_factory)
    assert summary.shipped == 1
    assert seen and all(value == (None if legacy else 2) for value in seen)
    assert cli.main(["--resume", str(output), "--max-concurrency", "1", "--estimate"]) == 2
