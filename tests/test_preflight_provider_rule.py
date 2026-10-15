from pathlib import Path

import pytest

from content_pipeline.config import Settings, load_plan
from content_pipeline.pipeline import validate_inputs


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "model",
    [
        "groq:openai/gpt-oss-120b",
        "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        "google_genai:gemini-2.5-flash",
    ],
)
def test_preflight_accepts_well_formed_provider(model):
    slots = load_plan(ROOT, ROOT / "inputs/plan.json")

    validate_inputs(ROOT, slots, Settings(model_override=model))


@pytest.mark.parametrize(
    "model",
    [
        "Bad-Name:x",
        "openrouter:no-slash",
        "noseparator",
    ],
)
def test_preflight_rejects_invalid_model_reference(model):
    slots = load_plan(ROOT, ROOT / "inputs/plan.json")

    with pytest.raises(ValueError, match="^invalid configured model reference$"):
        validate_inputs(ROOT, slots, Settings(model_override=model))
