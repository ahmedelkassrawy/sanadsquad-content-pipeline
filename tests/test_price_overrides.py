"""Price overrides from config/prices.yaml: used for quotes, validated at load, snapshotted."""

import shutil
from pathlib import Path

import pytest

from content_pipeline import pipeline
from content_pipeline.config import Settings
from content_pipeline.model_client import PRICES_FILE, Prices, load_price_overrides
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from test_pipeline_end_to_end import DEFAULT_FOCUS, OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]


def test_override_price_is_used_and_labelled():
    prices = Prices(update=False, overrides={"mistralai:m": {"input": 0.2, "output": 0.4}})

    cost, source = prices.quote("mistralai:m", 1_000_000, 500_000)

    assert cost == pytest.approx(0.4)
    assert source == "config/prices.yaml"


def test_committed_overrides_load():
    assert (
        load_price_overrides(ROOT / PRICES_FILE)["mistralai:ministral-14b-latest"]["input"] == 0.2
    )


@pytest.mark.parametrize(
    "text",
    [
        "m:x:\n  input: 0.2\n",
        "m:x:\n  input: cheap\n  output: 1\n",
        "m:x:\n  input: -1\n  output: 1\n",
        "m:x:\n  input: .nan\n  output: 1\n",
        "m:x:\n  input: 1\n  output: .inf\n",
        "m:x: 0.2\n",
    ],
)
def test_malformed_override_names_the_model(tmp_path, text):
    path = tmp_path / "prices.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="m:x"):
        load_price_overrides(path)


async def test_resume_ignores_later_edits_to_the_live_prices_file(tmp_path, monkeypatch):
    root = tmp_path / "root"
    output = tmp_path / "run"
    models = OfflineModels()
    slots = [ResolvedSlot(slot_id="a", type="tech-thread", focus=DEFAULT_FOCUS)]
    seen = []
    real = pipeline.Prices

    def recording(**kwargs):
        seen.append(kwargs["overrides"])

        return real(**kwargs)

    monkeypatch.setattr(pipeline, "Prices", recording)

    for name in ["agents", "post_types", "inputs", "config", "skills"]:
        shutil.copytree(ROOT / name, root / name)

    with pytest.raises(RuntimeError, match="interrupted"):
        await run_pipeline(
            root,
            slots,
            output,
            Settings(advisor_enabled=False),
            models.factory,
            search_factory,
            interrupt_after=["lane:write"],
        )

    assert (output / "snapshot" / PRICES_FILE).exists()

    (root / PRICES_FILE).write_text(
        "mistralai:ministral-14b-latest:\n  input: 99\n  output: 99\n", encoding="utf-8"
    )

    await run_pipeline(
        root, resume=output, model_factory=models.factory, search_factory=search_factory
    )

    assert seen[0] == seen[-1]
    assert seen[-1]["mistralai:ministral-14b-latest"]["input"] == 0.2
