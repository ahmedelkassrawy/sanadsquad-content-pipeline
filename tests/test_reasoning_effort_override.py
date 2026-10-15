import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from content_pipeline import cli, config, live_proof, model_client
from content_pipeline.config import Settings, load_agent
from content_pipeline.durability import resume_inputs, snapshot_hashes
from content_pipeline.ledger import Ledger
from content_pipeline.models import Review, ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline import FixturePrices, OfflineModels, search_factory


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    monkeypatch.setattr(config, "dotenv_values", lambda *args, **kwargs: {})
    monkeypatch.setattr("dotenv.dotenv_values", lambda *args, **kwargs: {})


@pytest.mark.parametrize("role,configured", [("reviewer", "medium"), ("thread_writer", "low")])
@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", None])
def test_configured_client_effort(tmp_path, role, configured, effort):
    assert load_agent(ROOT, role).reasoning_effort == configured
    settings = Settings() if effort is None else Settings(reasoning_effort_override=effort)
    client = model_client.configured_client(
        ROOT, role, Ledger(tmp_path / "ledger.json", 10000, 1),
        tmp_path / "usage.jsonl", settings=settings, prices=FixturePrices(),
    )
    assert isinstance(client.transport, model_client.LangChainTransport)
    assert client.transport.reasoning_effort == (configured if effort is None else effort)


def test_settings_rejects_invalid_effort():
    with pytest.raises(ValidationError):
        Settings(reasoning_effort_override="extreme")


def test_settings_loads_effort_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT_OVERRIDE", "none")
    assert Settings.load(tmp_path).reasoning_effort_override == "none"
    assert Settings.load(tmp_path, {"reasoning_effort_override": "high"}).reasoning_effort_override == "high"


def test_cli_resume_refuses_effort_override(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("resume override reached saved inputs")

    monkeypatch.setattr(cli, "resume_inputs", forbidden)
    assert cli.main(["--resume", str(tmp_path / "run"), "--reasoning-effort-override", "none"]) == 2
    assert "pipeline_error:ValueError" in capsys.readouterr().err


def test_cli_passes_effort_override_to_settings(tmp_path, monkeypatch):
    seen = []

    def estimate(root, slots, settings):
        seen.append(settings)
        return SimpleNamespace(model_dump_json=lambda **kwargs: "{}")

    monkeypatch.setattr(cli, "estimate", estimate)
    assert cli.main(["--root", str(ROOT), "--estimate", "--reasoning-effort-override", "none"]) == 0
    assert seen[0].reasoning_effort_override == "none"


@pytest.mark.parametrize("effort", ["none", None])
async def test_offline_proof_effort_settings_and_provenance(tmp_path, monkeypatch, effort):
    parsed = []
    original_parse = argparse.ArgumentParser.parse_args

    def parse(parser, *args, **kwargs):
        result = original_parse(parser, *args, **kwargs)
        parsed.append(result)
        return result

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse)
    output = tmp_path / "proof"
    flags = [] if effort is None else ["--reasoning-effort", effort]
    # Exercise the real parser with the live gate closed, then execute only fakes.
    assert live_proof.main(["--root", str(ROOT), "--output", str(output), *flags]) == 0
    assert not output.exists()
    seen = {}

    def client(root, role, ledger, usage, *, settings, prices):
        seen["reviewer"] = settings
        return object()

    class Reviewer:
        def __init__(self, *args):
            pass

        async def __call__(self, request):
            supported = request.slot_id == "supported"
            return Review(slot_id=request.slot_id, scores={"grounding": 5 if supported else 1},
                          reasons=[], hard_fail=[] if supported else ["unsupported_claim"], accepted=supported)

    async def pipeline(root, slots, path, settings):
        assert json.loads((output / "grounding/report.json").read_text())["verdict"] == "PASS"
        seen["sample"] = settings
        return SimpleNamespace(shipped=6, model_dump=lambda **kwargs: {"shipped": 6})

    monkeypatch.setattr(live_proof, "configured_client", client)
    monkeypatch.setattr(live_proof, "Reviewer", Reviewer)
    monkeypatch.setattr(live_proof, "Prices", lambda **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(live_proof, "run_pipeline", pipeline)
    assert await live_proof.execute(parsed[0]) == 0
    assert set(seen) == {"reviewer", "sample"}
    assert all(settings.reasoning_effort_override == effort for settings in seen.values())
    for name in ["sample-provenance.json", "grounding/report.json"]:
        assert json.loads((output / name).read_text())["reasoning_effort_override"] == effort


async def test_snapshot_without_effort_field_resumes(tmp_path):
    output = tmp_path / "run"
    models = OfflineModels()
    slots = [ResolvedSlot(slot_id="a", type="tech-thread", focus="reentrancy")]
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(ROOT, slots, output, Settings(advisor_enabled=False),
                           models.factory, search_factory, interrupt_after=["lane:write"])
    for path in [output / "snapshot/resolved-config.json", output / "runtime-settings.json"]:
        saved = json.loads(path.read_text())
        saved.pop("reasoning_effort_override", None)
        path.write_text(json.dumps(saved), encoding="utf-8")
    identity_path = output / "run_identity.json"
    identity = json.loads(identity_path.read_text())
    identity["snapshot_files"] = snapshot_hashes(output / "snapshot")
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    assert resume_inputs(output)[2].reasoning_effort_override is None
    summary = await run_pipeline(ROOT, resume=output, model_factory=models.factory, search_factory=search_factory)
    assert summary.shipped == 1
