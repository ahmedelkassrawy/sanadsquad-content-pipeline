"""Research topic proposal reservation and angle-file preflight before any model call."""

import json
import shutil
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import pytest
from content_pipeline.cli import main
from content_pipeline.models import ResolvedSlot
from content_pipeline.pipeline import run_pipeline
from content_pipeline.post_types import PostTypes
from content_pipeline.research import KnowledgeBase, assign_topics
from test_pipeline_end_to_end import OfflineModels, search_factory

ROOT = Path(__file__).resolve().parents[1]
MARKETING_IDS = [angle.id for angle in PostTypes(ROOT).angles("marketing-post")]
# One slot more than there are angles, so the last slot finds the pool exhausted.
SLOT_IDS = [chr(97 + i) for i in range(len(MARKETING_IDS) + 1)]


class ProposedModels(OfflineModels):
    def __init__(self, proposals):
        super().__init__()
        self.proposals = proposals

    def factory(self, name, slot_id, context):
        client = super().factory(name, slot_id, context)
        original = client.transport

        async def transport(messages, schema, max_tokens):
            result = await original(messages, schema, max_tokens)
            if schema.__name__ == "TopicChoices":
                result["parsed"]["assignments"] = self.proposals
            return result

        client.transport = transport
        return client


@pytest.mark.parametrize(
    "type_name,proposal", [("tech-thread", "SS-03738"), ("marketing-post", MARKETING_IDS[0])]
)
async def test_later_valid_topic_proposal_is_reserved_before_earlier_fallback(
    tmp_path, type_name, proposal
):
    models = ProposedModels({"b": proposal})
    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id=id, type=type_name) for id in ["a", "b"]],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.shipped == 2
    assert summary.slots["b"].topic_id == proposal
    assert summary.slots["a"].topic_id != proposal
    if type_name == "marketing-post":
        assert summary.slots["a"].topic_id == MARKETING_IDS[1]
    posts = json.loads((tmp_path / "run/posts.json").read_text())
    assert [(post["slot_id"], post["topic_id"]) for post in posts] == [
        ("a", summary.slots["a"].topic_id),
        ("b", proposal),
    ]


async def test_duplicate_proposal_precedence_and_other_valid_proposals(tmp_path):
    models = ProposedModels({"a": "SS-03738", "b": "SS-03738", "c": "SS-03703"})
    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id=id, type="tech-thread") for id in ["a", "b", "c"]],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.shipped == 3
    assert summary.slots["a"].topic_id == "SS-03738"
    assert summary.slots["c"].topic_id == "SS-03703"
    assert summary.slots["b"].topic_id not in {"SS-03738", "SS-03703"}


async def test_later_valid_angle_survives_pool_exhaustion(tmp_path):
    models = ProposedModels({"e": MARKETING_IDS[0]})
    summary = await run_pipeline(
        ROOT,
        [ResolvedSlot(slot_id=id, type="marketing-post") for id in SLOT_IDS],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    # The angle pool is exhausted by the last slot; independent quality lanes may drop, but selection still
    # reserves the later valid proposal instead of discarding it with the exhausted slot.
    assert summary.shipped >= 3
    assert summary.slots["e"].topic_id == MARKETING_IDS[0]
    assert summary.slots[SLOT_IDS[-1]].outcome.value == "failed_research"


def test_reserved_proposals_keep_history_focus_exclusions_and_type_pools():
    kb = KnowledgeBase(ROOT / "inputs/kb.json")
    slots = [
        ResolvedSlot(
            slot_id="focused", type="tech-thread", focus="oracle manipulation", topic_id="SS-03738"
        ),
        ResolvedSlot(slot_id="a", type="tech-thread"),
        ResolvedSlot(slot_id="b", type="tech-thread"),
        ResolvedSlot(slot_id="c", type="marketing-post"),
        ResolvedSlot(slot_id="d", type="marketing-post"),
    ]
    assigned, warnings = assign_topics(
        ROOT,
        slots,
        {"b": "SS-03703", "c": "SS-03738", "d": MARKETING_IDS[1]},
        kb,
        [["SS-03738", MARKETING_IDS[0]]],
    )
    assert assigned["focused"] == "SS-03738"
    assert assigned["b"] == "SS-03703"
    assert assigned["a"] not in {"SS-03738", "SS-03703"}
    assert assigned["c"] == MARKETING_IDS[2]
    assert assigned["d"] == MARKETING_IDS[1]
    # c asked for a KB id outside its angle pool (and one from recent history): rejected and logged.
    assert warnings == ["topic_proposal_rejected:c:SS-03738"]


def config_root(tmp_path, text=None, missing_file=False):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    if missing_file:
        file = root / "post_types/marketing-post/type.yaml"
        file.write_text(file.read_text().replace("angles: angles.yaml", "angles: absent.yaml"))
    else:
        (root / "post_types/marketing-post/angles.yaml").write_text(text)
    return root


INVALID_ANGLES = [
    "angles: broken-scalar\n",
    "angles: []\n",
    "angles:\n- {id: ANGLE-one}\n",
    "angles:\n- {name: service}\n",
    "angles:\n- {id: ANGLE-one, name: ' '}\n",
    "angles:\n- {id: ANGLE-one, name: service}\n- {id: ANGLE-one, name: duplicate}\n",
    "angles:\n- not-an-object\n",
    "angles:\n- {id: 12, name: service}\n",
    "angles:\n- {id: ' ', name: service}\n",
    "angles:\n- {id: ANGLE-one, name: 100}\n",
]


@pytest.mark.parametrize("text", INVALID_ANGLES)
async def test_invalid_angles_python_preflight_before_any_factory_call(tmp_path, text):
    root = config_root(tmp_path, text)
    models = OfflineModels()
    initialized = []

    def model_factory(name, slot_id, context):
        initialized.append((name, slot_id))
        return models.factory(name, slot_id, context)

    def search(root):
        initialized.append(("search", ""))
        return search_factory(root)

    error = None
    try:
        await run_pipeline(
            root,
            [
                ResolvedSlot(slot_id="a", type="tech-thread", focus="reentrancy"),
                ResolvedSlot(slot_id="b", type="marketing-post"),
            ],
            tmp_path / "run",
            model_factory=model_factory,
            search_factory=search,
        )
    except Exception as caught:
        error = caught
    assert initialized == [], "invalid configuration initialized model/search factories"
    assert isinstance(error, ValueError)
    assert models.calls == []
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("text", [INVALID_ANGLES[0], INVALID_ANGLES[4], INVALID_ANGLES[5]])
def test_invalid_angles_cli_preflight_before_any_factory_call(tmp_path, text):
    root = config_root(tmp_path, text)
    models = OfflineModels()
    initialized = []

    def model_factory(name, slot_id, context):
        initialized.append((name, slot_id))
        return models.factory(name, slot_id, context)

    result = main(
        ["--root", str(root), "--output", str(tmp_path / "run")],
        model_factory=model_factory,
        search_factory=search_factory,
    )
    assert result == 2
    assert initialized == []
    assert models.calls == []
    assert not (tmp_path / "run").exists()


def test_missing_angle_file_is_already_a_no_call_startup_error(tmp_path):
    root = config_root(tmp_path, missing_file=True)

    def forbidden(*args):
        pytest.fail("missing configuration initialized transport")

    assert (
        main(
            ["--root", str(root), "--output", str(tmp_path / "run")],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 2
    )
    assert not (tmp_path / "run").exists()


def test_normal_cli_warns_with_pure_estimate_before_model_search_initialization(tmp_path):
    stdout, stderr = StringIO(), StringIO()
    models = OfflineModels()
    initialized_after_warning = []

    def model_factory(name, slot_id, context):
        initialized_after_warning.append("startup_budget_warning:" in stderr.getvalue())
        return models.factory(name, slot_id, context)

    def search(root):
        initialized_after_warning.append("startup_budget_warning:" in stderr.getvalue())
        return search_factory(root)

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = main(
            ["--root", str(ROOT), "--token-budget", "1", "--output", str(tmp_path / "run")],
            model_factory=model_factory,
            search_factory=search,
        )
    assert result == 0
    assert "startup_budget_warning:" in stderr.getvalue()
    warning = json.loads(stderr.getvalue().split("startup_budget_warning:", 1)[1].strip())
    assert warning["token_budget"] == 1
    assert warning["worst_case_tokens"] > warning["token_budget"]
    assert "worst_case_exceeds_budget" in warning["warnings"]
    assert initialized_after_warning and all(initialized_after_warning)
    assert models.calls == []
    assert json.loads(stdout.getvalue())["shipped"] == 0
