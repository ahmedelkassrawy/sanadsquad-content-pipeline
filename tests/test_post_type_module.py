"""A new post type ships as folders only, and its files are read once per run."""

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
import yaml
from test_pipeline_end_to_end import OfflineModels, search_factory

from content_pipeline import model_client
from content_pipeline.config import Settings, load_prompt, load_type
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import default_model_factory, run_pipeline, validate_inputs
from content_pipeline.research import KnowledgeBase

ROOT = Path(__file__).resolve().parents[1]


def copy_inputs(destination):
    """Copy the repo inputs a run reads into a temp root."""
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, destination / folder)

    return destination


def add_case_study(root):
    """Add a third post type as folders and text only: no Python is touched."""
    folder = root / "post_types/case-study"

    shutil.copytree(root / "post_types/marketing-post", folder)

    type_file = folder / "type.yaml"

    type_file.write_text(type_file.read_text().replace("name: marketing-post", "name: case-study"))

    (folder / "angles.yaml").write_text(
        "angles:\n- {id: ANGLE-case-1, name: customer-case}\n- {id: ANGLE-case-2, name: lesson}\n"
    )


async def test_third_post_type_ships_as_folders_only(tmp_path):
    root = copy_inputs(tmp_path / "root")

    add_case_study(root)

    models = OfflineModels()
    output = tmp_path / "run"

    summary = await run_pipeline(
        root,
        [
            ResolvedSlot(slot_id="a", type="tech-thread"),
            ResolvedSlot(slot_id="b", type="marketing-post"),
            ResolvedSlot(slot_id="c", type="case-study"),
        ],
        output,
        model_factory=models.factory,
        search_factory=search_factory,
    )

    assert summary.shipped == 3
    assert summary.slots["c"].topic_id in {"ANGLE-case-1", "ANGLE-case-2"}

    posts = {post["slot_id"]: post for post in json.loads((output / "posts.json").read_text())}

    assert posts["c"]["type"] == "case-study"
    assert posts["c"]["tweets"] == ["Sentinel Grove is a smart contract audit firm."]

    writer = next(call for call in models.calls if call[2] == "Draft" and call[1] == "c")

    assert writer[0] == "marketing_writer"
    assert writer[3]["post_type"]["name"] == "case-study"
    assert not any(call[2] == "ResearchAction" for call in models.calls)


async def test_post_type_files_are_parsed_once_per_run(tmp_path, monkeypatch):
    root = copy_inputs(tmp_path / "root")

    add_case_study(root)

    reads = Counter()
    original = Path.read_text

    def counting_read(self, *args, **kwargs):
        reads[self.as_posix()] += 1

        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read)

    models = OfflineModels()
    output = tmp_path / "run"
    current = {}

    class OfflineTransport:
        """Stand in for the network transport only; the client itself is the real one."""

        def __init__(self, *args):
            self.send = models.factory(*current["call"]).transport

        async def __call__(self, messages, schema, max_tokens):
            return await self.send(messages, schema, max_tokens)

    monkeypatch.setattr(model_client, "LangChainTransport", OfflineTransport)

    def factory(name, slot_id, context):
        current["call"] = (name, slot_id, context)

        return default_model_factory(name, slot_id, context)

    summary = await run_pipeline(
        root,
        [
            ResolvedSlot(slot_id="a", type="tech-thread"),
            ResolvedSlot(slot_id="b", type="marketing-post"),
            ResolvedSlot(slot_id="c", type="case-study"),
            ResolvedSlot(slot_id="d", type="tech-thread"),
        ],
        output,
        settings=Settings(allow_unpriced=True),
        model_factory=factory,
        search_factory=search_factory,
    )

    assert summary.shipped == 4

    snapshot = (output / "snapshot").as_posix() + "/"
    loaded = {name: count for name, count in reads.items() if name.startswith(snapshot)}
    parsed = {
        name: count
        for name, count in loaded.items()
        if name.endswith(
            (
                "/type.yaml",
                "/agent.yaml",
                "/brand-voice.md",
                "/angles.yaml",
                "/rubric.md",
                "/research.md",
                "/writer.md",
                "/system.md",
            )
        )
    }

    brand = parsed.pop(snapshot + "inputs/brand-voice.md")
    fake_reads = sum(call[2] == "VisualOutput" for call in models.calls)

    assert len(parsed) >= 12
    assert {name: count for name, count in parsed.items() if count != 1} == {}
    # OfflineModels reads the brand palette itself for every visual call; the run reads it once.
    assert brand == 1 + fake_reads


def test_post_type_module_answers_writer_rubric_and_topic_pool():
    from content_pipeline.post_types import PostTypes, load_rubric

    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")

    assert types.writer("tech-thread") == "thread_writer"
    assert types.writer("marketing-post") == "marketing_writer"

    for name in ["tech-thread", "marketing-post"]:
        assert types.spec(name) == load_type(ROOT, name)
        assert types.rubric(name) == load_rubric(ROOT, load_type(ROOT, name))
        assert types.rubric(name).criteria
        assert types.prompt(types.writer(name), name) == load_prompt(ROOT, types.writer(name), name)
        assert types.prompt("reviewer", name) == load_prompt(ROOT, "reviewer", name)

    kb_ids = [entry["id"] for entry in kb.entries]
    angles = yaml.safe_load((ROOT / "post_types/marketing-post/angles.yaml").read_text())["angles"]

    assert types.topic_pool("tech-thread", kb) == kb_ids
    assert sorted(types.topic_pool("tech-thread", kb, ranked=True)) == sorted(kb_ids)
    assert types.topic_pool("marketing-post", kb) == [angle["id"] for angle in angles]
    assert types.topic_pool("marketing-post", kb, ranked=True) == types.topic_pool(
        "marketing-post", kb
    )


def test_post_type_module_assigns_research_packs_by_topic_source():
    from content_pipeline.post_types import PostTypes

    types = PostTypes(ROOT)
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    rules, _ = types.brand()

    from_kb = types.assigned_pack(
        ResolvedSlot(slot_id="a", type="tech-thread", topic_id="KB-01"), kb
    )

    assert [source.source_id for source in from_kb.sources] == ["KB-01"]

    from_angle = types.assigned_pack(
        ResolvedSlot(slot_id="b", type="marketing-post", topic_id="ANGLE-service"), kb
    )

    assert from_angle.topic_name == "service"
    # The service angle gets only its configured stats.
    assert {source.source_id: source.evidence_text for source in from_angle.sources} == {
        id: rules.approved_stats[id] for id in ["STAT-04", "STAT-05"]
    }

    assert types.assigned_pack(ResolvedSlot(slot_id="c", type="tech-thread"), kb) is None
    assert (
        types.assigned_pack(
            ResolvedSlot(slot_id="d", type="tech-thread", topic_id="KB-01", focus="x"), kb
        )
        is None
    )


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda text: text.replace("name: tech-thread", "name: other"), "differs from folder"),
        (lambda text: text.replace("topic_source: kb", "topic_source: nowhere"), "topic_source"),
    ],
)
def test_invalid_type_yaml_keeps_its_preflight_error(tmp_path, mutate, message):
    root = copy_inputs(tmp_path / "root")
    type_file = root / "post_types/tech-thread/type.yaml"

    type_file.write_text(mutate(type_file.read_text()))

    with pytest.raises(ValueError, match=message):
        validate_inputs(root, [ResolvedSlot(slot_id="a", type="tech-thread")])
