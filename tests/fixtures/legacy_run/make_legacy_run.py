"""Write an old-format run folder with the code of base commit 45ff432.

Run it from the root of a checkout of 45ff432 (before the S20 stage replay change):

    git worktree add --detach ../SanadSquad-s20-base 45ff432
    cd ../SanadSquad-s20-base
    python -B <path-to-this-file> <output-dir>
    git worktree remove --force ../SanadSquad-s20-base

The run uses the offline fake models of the test suite, one slot, and crashes right after the
validator record is saved, so the drafts trail has a record without an input fingerprint and
the lane node that wrote it has not checkpointed.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / "tests"))

from langgraph.errors import NodeCancelledError
from test_day3_delivery import ROOT, DeliveryModels, one_slot, search_factory

from content_pipeline.pipeline import Pipeline, run_pipeline


async def main(output):
    original = Pipeline.record

    async def record(pipeline, name, entry):
        await original(pipeline, name, entry)

        if name == "drafts":
            raise asyncio.CancelledError()

    models = DeliveryModels()

    with patch.object(Pipeline, "record", record):
        try:
            await run_pipeline(
                ROOT,
                one_slot(),
                output,
                model_factory=models.factory,
                search_factory=search_factory,
            )
        except NodeCancelledError:
            pass


asyncio.run(main(Path(sys.argv[1])))
