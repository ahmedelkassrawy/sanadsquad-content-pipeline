"""Exporting a run as the publishable sample output: exact contents, tamper and link rejection."""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from PIL import Image
from content_pipeline.config import load_plan
from content_pipeline.pipeline import run_pipeline
from content_pipeline.models import ImageSpec
from test_pipeline_resume_and_advisor_delivery import DeliveryModels
from test_pipeline_end_to_end import search_factory

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location(
    "sample_packager", ROOT / "scripts/export_run_as_sample_output.py"
)
packager = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(packager)


@pytest.fixture(scope="module", params=["png", "spec"])
def six_slot_source(tmp_path_factory, request):
    output = tmp_path_factory.mktemp("package-baseline") / "source-run"
    models = DeliveryModels()

    def unavailable_renderer(*args):
        raise OSError("synthetic renderer unavailable")

    kwargs = {"renderer": unavailable_renderer} if request.param == "spec" else {}
    summary = asyncio.run(
        run_pipeline(
            ROOT,
            load_plan(ROOT, ROOT / "inputs/plan.json"),
            output,
            model_factory=models.factory,
            search_factory=search_factory,
            **kwargs,
        )
    )
    assert summary.shipped == 6 and summary.uncertain_tokens == 0
    return output


def copied_source(tmp_path, six_slot_source):
    source = tmp_path / "source"
    shutil.copytree(six_slot_source, source)
    return source


def add_manifest_entry(source, name, data):
    identity = json.loads((source / "run_identity.json").read_text())
    identity["snapshot_files"][name] = hashlib.sha256(data).hexdigest()
    (source / "run_identity.json").write_text(json.dumps(identity))


def directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory links unavailable")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if result.returncode:
            pytest.skip("directory junctions unavailable")


def test_public_packager_copies_exact_six_slot_artifacts(six_slot_source, tmp_path):
    assert (six_slot_source / "research-replay.json").is_file()
    target = tmp_path / "package"
    packager.package(six_slot_source, target)
    manifest = json.loads((six_slot_source / "run_identity.json").read_text())["snapshot_files"]
    assert {
        path.relative_to(target / "snapshot").as_posix()
        for path in (target / "snapshot").rglob("*")
        if path.is_file()
    } == set(manifest)
    assert (target / "posts.csv").read_bytes() == (six_slot_source / "posts.csv").read_bytes()
    posts = json.loads((target / "posts.json").read_text())
    assert len(posts) == 6
    images = set()
    for post in posts:
        path = target / post["image"]
        images.update([path.name, path.with_suffix(".json").name])
        if path.suffix == ".png":
            with Image.open(path) as image:
                assert image.format == "PNG" and image.size == (1200, 675)
                image.verify()
        else:
            spec = ImageSpec.model_validate_json(path.read_bytes())
            assert spec.palette == ["#14213d", "#fca311", "#ffffff"]
    assert {path.name for path in (target / "images").iterdir()} == images
    assert len(list((target / "schemas").glob("*.json"))) == 56
    assert not any(
        (target / name).exists()
        for name in [
            "checkpoint.sqlite",
            "run.lock",
            "run_identity.json",
            "runtime-settings.json",
            "state.json",
            "research-replay.json",
        ]
    )


@pytest.mark.parametrize(
    "name,tamper",
    [
        ("snapshot/.env", False),
        ("images/private-note.txt", False),
        ("snapshot/private-note.md", True),
        ("snapshot/.env", True),
        ("private-note.txt", False),
    ],
)
def test_packager_rejects_private_extras_even_manifest_tampering(
    six_slot_source, tmp_path, name, tamper
):
    source = copied_source(tmp_path, six_slot_source)
    data = b"synthetic-private-canary-only"
    (source / name).write_bytes(data)
    if tamper:
        add_manifest_entry(source, name.removeprefix("snapshot/"), data)
    target = tmp_path / "package"
    with pytest.raises(ValueError):
        packager.package(source, target)
    assert not target.exists()


def test_packager_rejects_changed_snapshot_hash_before_output(six_slot_source, tmp_path):
    source = copied_source(tmp_path, six_slot_source)
    with (source / "snapshot/post_types/tech-thread/research.md").open("a") as stream:
        stream.write("Changed policy")
    target = tmp_path / "package"
    with pytest.raises(ValueError):
        packager.package(source, target)
    assert not target.exists()


def test_packager_rejects_corrupt_referenced_card_or_spec_before_output(six_slot_source, tmp_path):
    source = copied_source(tmp_path, six_slot_source)
    post = json.loads((source / "posts.json").read_text())[0]
    (source / post["image"]).write_bytes(b"synthetic corrupted image or spec")
    target = tmp_path / "package"
    with pytest.raises((ValueError, OSError)):
        packager.package(source, target)
    assert not target.exists()


def test_packager_rejects_mismatched_run_identity_before_output(six_slot_source, tmp_path):
    source = copied_source(tmp_path, six_slot_source)
    identity = json.loads((source / "run_identity.json").read_text())
    identity["run_id"] = "wrong-run"
    (source / "run_identity.json").write_text(json.dumps(identity))
    target = tmp_path / "package"
    with pytest.raises(ValueError):
        packager.package(source, target)
    assert not target.exists()


def test_packager_rejects_escaping_referenced_image_before_output(six_slot_source, tmp_path):
    source = copied_source(tmp_path, six_slot_source)
    for name in ["posts", "visuals", "final"]:
        records = json.loads((source / (name + ".json")).read_text())
        if name == "posts":
            records[0]["image"] = "../outside.png"
        else:
            records[0]["result" if name == "visuals" else "image"]["path"] = "../outside.png"
        (source / (name + ".json")).write_text(json.dumps(records))
    target = tmp_path / "package"
    with pytest.raises(ValueError):
        packager.package(source, target)
    assert not target.exists()


def test_packager_rejects_source_target_overlap_before_output(six_slot_source, tmp_path):
    source = copied_source(tmp_path, six_slot_source)
    target = source / "child-package"
    with pytest.raises(ValueError):
        packager.package(source, target)
    assert not target.exists()


@pytest.mark.parametrize("linked", ["source", "snapshot-directory", "target-parent"])
def test_packager_rejects_directory_links_even_matching_data(six_slot_source, tmp_path, linked):
    source = copied_source(tmp_path, six_slot_source)
    target = tmp_path / "package"
    if linked == "source":
        alias = tmp_path / "source-alias"
        directory_link(alias, source)
        source = alias
    elif linked == "snapshot-directory":
        skills = source / "snapshot/skills"
        outside = tmp_path / "skills-outside"
        assert skills.resolve().is_relative_to(
            source.resolve()
        ) and outside.resolve().is_relative_to(tmp_path.resolve())
        skills.rename(outside)
        directory_link(skills, outside)
    else:
        parent = tmp_path / "linked-parent"
        outside = tmp_path / "outside"
        outside.mkdir()
        directory_link(parent, outside)
        target = parent / "package"
    with pytest.raises(ValueError):
        packager.package(source, target)
    assert not target.exists()
