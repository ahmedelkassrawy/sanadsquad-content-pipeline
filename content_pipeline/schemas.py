import argparse
import json
from pathlib import Path
from pydantic import TypeAdapter
from . import models
from . import contracts
from .config import Settings


def export(output: Path):
    output.mkdir(parents=True, exist_ok=True)

    classes = {
        v.__name__: v
        for module in (models, contracts)
        for v in vars(module).values()
        if isinstance(v, type) and issubclass(v, models.Contract) and v is not models.Contract
    }

    for model in [*classes.values(), Settings]:
        (output / f"{model.__name__}.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
        )

    (output / "SlotOutcome.json").write_text(
        json.dumps(TypeAdapter(models.SlotOutcome).json_schema(), indent=2) + "\n", encoding="utf-8"
    )

    (output / "Ledger.json").write_text(
        json.dumps(TypeAdapter(dict[str, contracts.LedgerEntry]).json_schema(), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("schemas"))
    export(parser.parse_args().output)
