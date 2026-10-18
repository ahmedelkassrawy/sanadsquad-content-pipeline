"""Copy a finished run into the publishable sample output folder.

Checks the run first and leaves out private files (checkpoints, locks, credentials). Example:

    .venv/Scripts/python.exe scripts/export_run_as_sample_output.py --source <run-folder> --output <new-folder>
"""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content_pipeline.config import Settings
from content_pipeline.contracts import (
    AdvisorRecord,
    DraftRecord,
    ExportPost,
    FinalPiece,
    ResearchRecord,
    ResolvedPlan,
    ReviewRecord,
    RunIdentity,
    RunSummary,
    VisualRecord,
)
from content_pipeline.durability import RunLock
from content_pipeline.models import AgentConfig, ImageSpec, PostType, ResolvedSlot, UsageRecord
from content_pipeline.schemas import export


def boundary(path):
    path = Path(os.path.abspath(path))

    for component in [*reversed(path.parents), path]:
        if component.exists() or component.is_symlink():
            info = component.lstat()

            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("sample paths cannot contain links or reparse points")

    return path.resolve()


def relative_name(name):
    path = PurePosixPath(name)

    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or path.as_posix() != name
        or PureWindowsPath(name).anchor
    ):
        raise ValueError("invalid sample relative path")

    for part in path.parts:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
            or part in {".", ".."}
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0])
        ):
            raise ValueError("unsafe sample filename")

    return name


def inventory(root):
    files = {}
    directories = set()

    def visit(folder):
        for path in folder.iterdir():
            boundary(path)

            name = relative_name(path.relative_to(root).as_posix())
            info = path.lstat()

            if stat.S_ISDIR(info.st_mode):
                directories.add(name)

                visit(path)
            elif stat.S_ISREG(info.st_mode):
                files[name] = path
            else:
                raise ValueError("sample requires regular files")

    visit(root)

    if len({name.casefold() for name in files}) != len(files):
        raise ValueError("colliding sample filenames")

    return files, directories


def expected_snapshot(contents):
    expected = {
        "inputs/brand-voice.md",
        "inputs/kb.json",
        "inputs/plan.json",
        "resolved-config.json",
        "agents/research/loop.md",
        "agents/research/topics.md",
        "config/repair.md",
        "config/visual.yaml",
    }

    if "config/prices.yaml" in contents:
        expected.add("config/prices.yaml")

    slots = [
        ResolvedSlot.model_validate(slot)
        for slot in json.loads(contents["inputs/plan.json"])["slots"]
    ]

    if len(slots) != 6 or len({slot.slot_id.casefold() for slot in slots}) != 6:
        raise ValueError("sample needs six distinct slots")

    Settings.model_validate_json(contents["resolved-config.json"])

    agents = {"research", "reviewer", "visual", "advisor"}

    for slot in slots:
        if "/" in slot.type or "/" in slot.slot_id:
            raise ValueError("invalid sample slot/type")

        relative_name(slot.slot_id)

        path = relative_name(f"post_types/{slot.type}/type.yaml")
        spec = PostType.model_validate(yaml.safe_load(contents[path]))

        if spec.name != slot.type or "/" in spec.writer:
            raise ValueError("invalid snapshot type/writer")

        agents.add(spec.writer)

        expected.update([path, f"post_types/{slot.type}/writer.md"])

        for name, suffix in [
            (spec.rubric, ".md"),
            (spec.research.hints, ".md"),
            (spec.research.angles, ".yaml"),
        ]:
            if name:
                path = relative_name(f"post_types/{slot.type}/{name}")

                if PurePosixPath(path).suffix != suffix:
                    raise ValueError("invalid snapshot instruction extension")

                expected.add(path)

    for name in agents:
        path = relative_name(f"agents/{name}/agent.yaml")
        agent = AgentConfig.model_validate(yaml.safe_load(contents[path]))

        expected.update([path, relative_name(f"agents/{name}/system.md")])

        for skill in agent.skills:
            expected.update(relative_name(f"skills/{skill}{suffix}") for suffix in [".md", ".py"])

    return expected, slots


def validated_files(source):
    contracts = {
        "research_log": ResearchRecord,
        "drafts": DraftRecord,
        "review_report": ReviewRecord,
        "visuals": VisualRecord,
        "final": FinalPiece,
        "advisor": AdvisorRecord,
        "posts": ExportPost,
    }

    root_names = {
        *(name + ".json" for name in contracts),
        "plan.resolved.json",
        "run_summary.json",
        "usage.jsonl",
        "ledger.json",
        "external-usage.json",
        "posts.csv",
        "advice.md",
        "advice.patch",
        "run.log",
        "README.md",
    }

    private_names = {
        "run.lock",
        "run_identity.json",
        "runtime-settings.json",
        "state.json",
        "research-replay.json",
        "checkpoint.sqlite",
        "checkpoint.sqlite-wal",
        "checkpoint.sqlite-shm",
    }

    if {path.name for path in source.iterdir()} - (
        root_names | private_names | {"snapshot", "images"}
    ):
        raise ValueError("unexpected run artifact")

    for path in source.iterdir():
        boundary(path)

    snapshot_paths, snapshot_dirs = inventory(source / "snapshot")

    identity = RunIdentity.model_validate_json((source / "run_identity.json").read_bytes())
    manifest = {relative_name(name): digest for name, digest in identity.snapshot_files.items()}

    if set(snapshot_paths) != set(manifest):
        raise ValueError("snapshot manifest mismatch")

    snapshot = {name: path.read_bytes() for name, path in snapshot_paths.items()}

    if any(hashlib.sha256(data).hexdigest() != manifest[name] for name, data in snapshot.items()):
        raise ValueError("snapshot hash mismatch")

    try:
        expected, slots = expected_snapshot(snapshot)
    except (KeyError, TypeError):
        raise ValueError("incomplete snapshot structure") from None

    expected_dirs = {
        parent.as_posix()
        for name in expected
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }

    if set(snapshot) != expected or snapshot_dirs != expected_dirs:
        raise ValueError("unexpected snapshot structure")

    data = {name: (source / name).read_bytes() for name in root_names}
    summary = RunSummary.model_validate_json(data["run_summary.json"])

    if (
        summary.run_id != identity.run_id
        or not identity.thread_id
        or summary.shipped != 6
        or set(summary.slots) != {slot.slot_id for slot in slots}
        or any(slot.outcome.value != "shipped" for slot in summary.slots.values())
    ):
        raise ValueError("sample requires six shipped pieces and matching identity")

    records = {}

    for name, model in contracts.items():
        records[name] = [
            model.model_validate(record) for record in json.loads(data[name + ".json"])
        ]

        if len({record.record_id for record in records[name]}) != len(records[name]):
            raise ValueError("duplicate sample records")

    for line in data["usage.jsonl"].splitlines():
        UsageRecord.model_validate_json(line)

    resolved = ResolvedPlan.model_validate_json(data["plan.resolved.json"])
    posts = records["posts"]

    if (
        [post.slot_id for post in posts] != [slot.slot_id for slot in slots]
        or [slot.slot_id for slot in resolved.slots] != [slot.slot_id for slot in slots]
        or len(records["final"]) != 6
    ):
        raise ValueError("sample plan/export mismatch")

    image_paths, image_dirs = inventory(source / "images")

    images = {}

    for post in posts:
        final = next(
            (
                record
                for record in records["final"]
                if record.slot_id == post.slot_id and record.attempt == post.attempt
            ),
            None,
        )

        visual = next(
            (
                record
                for record in records["visuals"]
                if record.slot_id == post.slot_id and record.attempt == post.attempt
            ),
            None,
        )

        if (
            final is None
            or visual is None
            or final.image != visual.result
            or post.image != final.image.path
            or post.tweets != [tweet.text for tweet in final.draft.tweets]
            or (post.topic_id, post.topic_name) != (final.topic_id, final.topic_name)
        ):
            raise ValueError("sample result mismatch")

        suffix = {"pillow_card": ".png", "spec_json": ".json"}.get(final.image.backend)

        if suffix is None or post.image != f"images/slot-{post.slot_id}{suffix}":
            raise ValueError("unsafe sample image reference")

        filename = relative_name(post.image.removeprefix("images/"))
        spec_name = str(PurePosixPath(filename).with_suffix(".json"))

        if filename not in image_paths or spec_name not in image_paths:
            raise ValueError("missing sample image/spec")

        spec = image_paths[spec_name].read_bytes()

        if ImageSpec.model_validate_json(spec) != visual.spec:
            raise ValueError("sample spec mismatch")

        images[spec_name] = spec

        if suffix == ".png":
            from PIL import Image

            image_data = image_paths[filename].read_bytes()

            with Image.open(io.BytesIO(image_data)) as image:
                if image.format != "PNG" or image.size != (1200, 675):
                    raise ValueError("invalid sample card")

                image.verify()

            images[filename] = image_data

    if image_dirs or set(image_paths) != set(images):
        raise ValueError("unexpected image artifact")

    if list(csv.reader(io.StringIO(data["posts.csv"].decode("utf-8")))) != [
        ["Post Text", "Image"],
        *[["\n".join(post.tweets), post.image] for post in posts],
    ]:
        raise ValueError("sample CSV mismatch")

    return {
        **data,
        **{"snapshot/" + name: value for name, value in snapshot.items()},
        **{"images/" + name: value for name, value in images.items()},
    }


def package(source, target):
    source = boundary(source)
    target = boundary(target)

    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("sample destination exists or overlaps source")

    boundary(source / "run.lock")

    with RunLock(source):
        data = validated_files(source)

        target.mkdir(parents=True, exist_ok=False)

        for name, contents in data.items():
            path = target / name

            path.parent.mkdir(parents=True, exist_ok=True)

            path.write_bytes(contents)

        export(target / "schemas")

        (target / "PACKAGE.md").write_text(
            "Inspectable sample package. SQLite checkpoint, ownership lock and internal run identity/state are excluded; this copy cannot resume. Copy the live sample-provenance.json beside this package before claiming live provenance.\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.source, args.output)
