"""Run the whole pipeline with scripted fake models: no model or search calls, no LLM keys.

Cloudflare image generation is still used if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are
set; otherwise the card is drawn locally with Pillow. Verification only; the normal CLI never
uses these models. Example:

    .venv/Scripts/python.exe scripts/run_offline_demo_with_fake_models.py --output <new-folder>
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_pipeline_end_to_end import OfflineModels, search_factory  # noqa: E402
from content_pipeline.cli import main  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        "OFFLINE INJECTED FIXTURE: scripted model decisions; not live or semantic grounding proof.",
        file=sys.stderr,
    )
    raise SystemExit(
        main(
            ["--root", str(ROOT), "--output", str(args.output)],
            model_factory=OfflineModels().factory,
            search_factory=search_factory,
        )
    )
