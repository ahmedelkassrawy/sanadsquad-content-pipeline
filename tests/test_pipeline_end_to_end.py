"""Whole-pipeline runs with scripted fake models; also the shared offline test helpers."""

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from PIL import Image
from content_pipeline.config import load_brand, load_plan
from content_pipeline.model_client import ModelClient
from content_pipeline.models import ResolvedSlot, UsageRecord
from content_pipeline.pipeline import run_pipeline
from content_pipeline.research import WebSearch
from content_pipeline import contracts
from content_pipeline.cli import main
from run_piece import main as piece_main

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOCUS = "oracle manipulation"
DEFAULT_KB_ID = "SS-03738"
DEFAULT_KB_TITLE = "Non-Canonical Price Feeds Allow Historical Price Manipulation"


class FixturePrices:
    def quote(self, *args):
        return 0.00001, "offline-fixture"


class OfflineModels:
    def __init__(self, behavior=None, barriers=False):
        self.behavior = behavior or {}
        self.calls = []
        self.repairs = []
        self.research_barrier = asyncio.Barrier(2) if barriers else None
        self.write_barrier = asyncio.Barrier(2) if barriers else None

    def factory(self, name, slot_id, context):
        review_count = 0

        async def transport(messages, schema, max_tokens):
            nonlocal review_count
            payload = None
            for message in reversed(messages):
                try:
                    candidate = json.loads(message["content"])
                    if isinstance(candidate, dict):
                        payload = candidate
                        break
                except (ValueError, TypeError):
                    continue
            attempt = payload.get("attempt", 1) if payload else 1
            self.calls.append((name, slot_id, schema.__name__, payload, max_tokens))
            mode = self.behavior.get(slot_id, {})
            if schema.__name__ == "ResearchAction":
                request = payload["request"]
                focus = request["slot"]["focus"] or ""
                evidence = payload["evidence"]
                if (
                    self.research_barrier
                    and slot_id in {"a", "b"}
                    and payload["searches_remaining"] == 3
                ):
                    await self.research_barrier.wait()
                action = "finish"
                web = [
                    source["source_id"]
                    for source in evidence
                    if source["kind"] == "web" and source["status"] == "ok"
                ]
                if focus in {"unknown", "endless", "no-evidence", "fetch-failed"} and (
                    not web or focus == "endless"
                ):
                    action = "web_search"
                selected = web[:1] if web else [evidence[0]["source_id"]]
                parsed = {
                    "slot_id": slot_id,
                    "attempt": 1,
                    "action": action,
                    "query": focus,
                    "source_ids": selected,
                    "topic_id": selected[0] if selected and selected[0].startswith("SS-") else None,
                }
                if mode.get("research_mismatch"):
                    parsed["slot_id"] = "other"
            elif schema.__name__ == "TopicChoices":
                parsed = {"slot_id": "_batch", "attempt": 1, "assignments": {}}
            elif schema.__name__ == "Draft":
                if self.write_barrier and slot_id in {"a", "b"} and attempt == 1:
                    await self.write_barrier.wait()
                if mode.get("api"):
                    error = RuntimeError("synthetic-key-must-not-leak")
                    error.status_code = 401
                    raise error
                source_id = payload["research"]["sources"][0]["source_id"]
                text = (
                    "State before calls."
                    if source_id.startswith("SS-")
                    else "Sentinel Grove is a smart contract audit firm."
                    if source_id.startswith("STAT-")
                    else (
                        f"Read {payload['research']['sources'][0]['url']} for source evidence."
                        if payload["research"]["sources"][0].get("url")
                        and (
                            payload["post_type"]["name"] == "tech-thread"
                            or (payload.get("format") or {}).get("link_rule") == "required"
                        )
                        else "Source evidence supports this topic."
                    )
                )
                text = mode.get("writer_text", text)
                if attempt in mode.get("invalid_attempts", []):
                    source_id = "SS-99"
                source_ids = mode.get("writer_source_ids", [source_id])
                parsed = {
                    "slot_id": "other" if mode.get("writer_mismatch") else slot_id,
                    "attempt": attempt,
                    "tweets": [
                        {"text": text, "source_ids": source_ids}
                        for _ in range(payload["post_type"]["units"]["min"])
                    ],
                }
            elif schema.__name__ == "ReviewAssessment":
                review_count += 1
                if messages[-1]["content"].startswith("The previous response"):
                    self.repairs.append(messages[-1]["content"])
                criteria = payload["rubric"]["criteria"]
                hard = payload["post_type"]["review"]["hard_fail"]
                unsupported = attempt in mode.get("reject_attempts", [])
                parsed = {
                    "slot_id": "other" if mode.get("review_mismatch") else slot_id,
                    "attempt": attempt,
                    "scores": {key: 4.0 for key in criteria},
                    "reasons": {key: "Supported by supplied evidence." for key in criteria},
                    "hard_fail": {key: unsupported and key == "unsupported_claim" for key in hard},
                }
                if unsupported:
                    parsed["reasons"]["unsupported_claim"] = "Unsupported claim must be removed."
                if mode.get("missing_scores"):
                    parsed["scores"] = {criteria[0]: 5}
                if mode.get("missing_once") and review_count == 1:
                    parsed["scores"] = {criteria[0]: 5}
                if mode.get("low_scores"):
                    parsed["scores"] = {key: 1.0 for key in criteria}
                if mode.get("fake_approval"):
                    parsed["accepted"] = True
            elif schema.__name__ == "VisualOutput":
                if mode.get("visual_api"):
                    raise RuntimeError("synthetic-key-must-not-leak")
                overlay = mode.get("overlay", payload["draft"]["tweets"][0]["text"])
                parsed = {
                    "slot_id": slot_id,
                    "attempt": attempt,
                    "spec": {
                        "concept": "Card",
                        "scene": "Card",
                        "palette": load_brand(context.root / "inputs/brand-voice.md")[0].palette,
                        "overlay_text": overlay,
                        "alt_text": overlay,
                        "severity": payload["severity"]
                        if payload["post_type"]["visual"]["severity_badge"] == "auto"
                        else None,
                    },
                }
            elif schema.__name__ == "AdvisorOutput":
                parsed = {
                    "slot_id": slot_id,
                    "attempt": attempt,
                    "recommendations": [],
                    "edits": [],
                }
            else:
                raise AssertionError("unexpected schema " + schema.__name__)
            return {
                "raw": SimpleNamespace(
                    content=json.dumps(parsed),
                    usage_metadata={"input_tokens": 20, "output_tokens": 30},
                    response_metadata={"finish_reason": "stop"},
                ),
                "parsed": parsed,
                "parsing_error": None,
            }

        return ModelClient(
            "openai:gpt-4.1-mini",
            context.ledger,
            context.run_dir / "usage.jsonl",
            transport=transport,
            prices=FixturePrices(),
            repair_prompt=context.root / "config/repair.md",
            usage_lock=context.usage_lock,
        )


class OfflineSearch:
    def search(self, **kwargs):
        if kwargs["query"] == "no-evidence":
            return {"results": []}
        if kwargs["query"] == "fetch-failed":
            return {
                "results": [
                    {
                        "url": "https://example.com/failed",
                        "title": "Failed fixture source",
                        "content": "summary only",
                    }
                ]
            }
        return {
            "results": [
                {
                    "url": "https://github.com/evidence",
                    "title": "Public fixture source",
                    "raw_content": "Source evidence supports this topic.",
                }
            ]
        }


def search_factory(root):
    return WebSearch(tavily=OfflineSearch(), check_url=lambda url: url, fetch=lambda url: "")


def plan_pair():
    return [
        ResolvedSlot(slot_id="a", type="tech-thread", focus=DEFAULT_FOCUS),
        ResolvedSlot(slot_id="b", type="tech-thread", focus=DEFAULT_FOCUS),
    ]


async def run(tmp_path, models, slots=None, **kwargs):
    return await run_pipeline(
        ROOT,
        slots or plan_pair(),
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
        **kwargs,
    )


async def test_public_six_slot_run_produces_schema_valid_trails_png_and_order(tmp_path):
    models = OfflineModels()
    slots = load_plan(ROOT, ROOT / "inputs/plan.json")
    summary = await run(tmp_path, models, slots)
    assert summary.shipped == 6
    assert [slot.outcome.value for slot in summary.slots.values()] == ["shipped"] * 6
    folder = tmp_path / "run"
    posts = json.loads((folder / "posts.json").read_text())
    assert [post["slot_id"] for post in posts] == [slot.slot_id for slot in slots]
    assert posts[0]["tweets"] == [f"{i}/5 State before calls." for i in range(1, 6)]
    for post in posts:
        contracts.ExportPost.model_validate(post)
        image = Image.open(folder / post["image"])
        assert (image.format, image.size) == ("PNG", (1200, 675))
    files = {
        "research_log": contracts.ResearchRecord,
        "drafts": contracts.DraftRecord,
        "review_report": contracts.ReviewRecord,
        "visuals": contracts.VisualRecord,
        "final": contracts.FinalPiece,
    }
    for name, model in files.items():
        records = json.loads((folder / (name + ".json")).read_text())
        assert len({record["record_id"] for record in records}) == len(records)
        for record in records:
            model.model_validate(record)
            assert record["slot_id"] in {slot.slot_id for slot in slots}
            assert record["attempt"] == 1
    writer = next(call[3] for call in models.calls if call[2] == "Draft" and call[1] == "thread_1")
    review = next(
        call[3] for call in models.calls if call[2] == "ReviewAssessment" and call[1] == "thread_1"
    )
    assert writer["research"]["sources"] == review["research"]["sources"]
    assert review["draft"]["tweets"][0]["text"] == posts[0]["tweets"][0]
    usage = [
        UsageRecord.model_validate_json(line)
        for line in (folder / "usage.jsonl").read_text().splitlines()
    ]
    assert len({record.call_id for record in usage}) == len(usage)
    assert summary.confirmed_tokens == len(usage) * 50
    assert (summary.external_usage.image_calls, summary.external_usage.pillow_cards) == (6, 6)
    with sqlite3.connect(folder / "checkpoint.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM checkpoints").fetchone()[0] > 3
    saved = contracts.WeekSnapshot.model_validate_json((folder / "state.json").read_text())
    research_record = next(
        record
        for record in json.loads((folder / "research_log.json").read_text())
        if record["slot_id"] == "thread_1" and record["pack"] is not None
    )
    assert saved.slots["thread_1"].pack.model_dump(mode="json") == research_record["pack"]
    assert saved.summary.shipped == 6
    assert (
        saved.slots["thread_1"].audit.approved().model_dump()
        == contracts.FinalPiece.model_validate(
            next(
                piece
                for piece in json.loads((folder / "final.json").read_text())
                if piece["slot_id"] == "thread_1"
            )
        ).draft.model_dump()
    )

    log = (folder / "run.log").read_text()

    assert "== thread_1" in log
    assert "review 1   accepted" in log
    assert "outcome    shipped after 1 attempt(s)" in log
    assert "shipped 6, dropped or failed 0" in log

    index = (folder / "README.md").read_text()

    assert "`posts.csv`" in index
    assert "`run.log`" in index


async def test_focused_research_and_write_overlap_barriers(tmp_path):
    models = OfflineModels(barriers=True)
    summary = await asyncio.wait_for(run(tmp_path, models), 15)
    assert summary.shipped == 2
    assert summary.slots["a"].topic_id == DEFAULT_KB_ID
    assert summary.slots["b"].topic_id == DEFAULT_KB_ID
    assert json.loads((tmp_path / "run/plan.resolved.json").read_text())["warnings"] == [
        f"explicit_duplicate:{DEFAULT_KB_ID}"
    ]


async def test_validator_guard_never_reaches_reviewer_or_visual(tmp_path):
    models = OfflineModels({"a": {"invalid_attempts": [1, 2]}})
    summary = await run(tmp_path, models, [plan_pair()[0]])
    assert summary.slots["a"].outcome.value == "dropped_quality"
    assert summary.slots["a"].attempts == 2
    assert [call[2] for call in models.calls if call[1] == "a"] == [
        "ResearchAction",
        "Draft",
        "Draft",
    ]
    assert (tmp_path / "run/posts.csv").read_bytes() == b"Post Text,Image\r\n"


@pytest.mark.parametrize("first_validator", [True, False])
async def test_shared_content_retry_drops_after_mixed_rejections(tmp_path, first_validator):
    behavior = {
        "invalid_attempts": [1] if first_validator else [2],
        "reject_attempts": [2] if first_validator else [1],
    }
    models = OfflineModels({"a": behavior})
    summary = await run(tmp_path, models, [plan_pair()[0]])
    assert summary.slots["a"].outcome.value == "dropped_quality"
    assert len([call for call in models.calls if call[2] == "Draft"]) == 2
    assert len([call for call in models.calls if call[2] == "ReviewAssessment"]) == 1
    second = next(
        call[3] for call in models.calls if call[2] == "Draft" and call[3]["attempt"] == 2
    )
    assert (
        second["feedback"]
        == [f"tweet_{i}:citation:SS-99" for i in range(1, 6)] + ["thread_citation_required"]
        if first_validator
        else second["feedback"] == ["Unsupported claim must be removed."]
    )
    assert json.loads((tmp_path / "run/visuals.json").read_text()) == []


async def test_reviewer_guard_rejects_unsupported_claim(tmp_path):
    models = OfflineModels({"a": {"reject_attempts": [1, 2]}})
    summary = await run(tmp_path, models, [plan_pair()[0]])
    assert summary.shipped == 0
    assert summary.slots["a"].outcome.value == "dropped_quality"
    assert summary.slots["a"].reasons == ["Unsupported claim must be removed."]


@pytest.mark.parametrize(
    "mode,outcome",
    [
        ("api", "failed_api"),
        ("missing_scores", "failed_output"),
        ("fake_approval", "failed_output"),
        ("review_mismatch", "failed_output"),
        ("research_mismatch", "failed_research"),
        ("writer_mismatch", "dropped_quality"),
    ],
)
async def test_slot_failure_isolation_and_fail_closed_output(tmp_path, mode, outcome):
    models = OfflineModels({"a": {mode: True}})
    summary = await run(tmp_path, models)
    assert summary.slots["a"].outcome.value == outcome
    assert summary.slots["b"].outcome.value == "shipped"
    assert [post["slot_id"] for post in json.loads((tmp_path / "run/posts.json").read_text())] == [
        "b"
    ]
    if mode in {"missing_scores", "fake_approval", "review_mismatch"}:
        assert (
            len([call for call in models.calls if call[1] == "a" and call[2] == "ReviewAssessment"])
            == 2
        )
    for path in [tmp_path / "run/usage.jsonl", tmp_path / "run/run_summary.json"]:
        assert "synthetic-key-must-not-leak" not in path.read_text()


async def test_a_failed_api_slot_records_its_error_in_the_summary_and_log(tmp_path):
    summary = await run(tmp_path, OfflineModels({"a": {"api": True}}))

    error = summary.slots["a"].error
    saved = json.loads((tmp_path / "run/run_summary.json").read_text())["slots"]["a"]
    log = (tmp_path / "run/run.log").read_text()

    assert summary.slots["a"].outcome.value == "failed_api"
    assert error and error.startswith("ModelFailure:")
    assert saved["error"] == error
    assert f"   error      {error}" in log
    assert summary.slots["b"].error is None
    # The provider message (a stand-in for a key) never reaches the run folder.
    for path in (tmp_path / "run").rglob("*"):
        if path.is_file() and path.suffix in {".json", ".log", ".jsonl", ".md"}:
            assert "synthetic-key-must-not-leak" not in path.read_text(encoding="utf-8")


async def test_a_focused_slot_fails_rather_than_ship_a_different_kb_topic(tmp_path):
    # The fake research finishes on the top KB hit, which is not about the requested topic.
    focus = "Zorvex ledger re-pricing exploit in Cairo 9 vaults"
    slots = [ResolvedSlot(slot_id="a", type="tech-thread", focus=focus), plan_pair()[1]]

    summary = await run(tmp_path, OfflineModels(), slots)

    assert summary.slots["a"].outcome.value == "failed_research"
    assert summary.slots["a"].error == (
        "ModelFailure: failed_research (topic_does_not_match_focus)"
    )
    assert summary.slots["b"].outcome.value == "shipped"


@pytest.mark.parametrize("focus", ["unknown", "endless", "no-evidence", "fetch-failed"])
async def test_research_web_fallback_retained_evidence_and_step_cap(tmp_path, focus):
    models = OfflineModels()
    slots = [ResolvedSlot(slot_id="a", type="tech-thread", focus=focus), plan_pair()[1]]
    summary = await run(tmp_path, models, slots)
    assert summary.slots["b"].outcome.value == "shipped"
    assert summary.slots["a"].outcome.value == (
        "shipped" if focus == "unknown" else "failed_research"
    )
    assert summary.external_usage.search_calls == (1 if focus == "unknown" else 3)
    evidence = json.loads((tmp_path / "run/research_log.json").read_text())
    web = [
        source for record in evidence for source in record["evidence"] if source["kind"] == "web"
    ]
    if focus == "unknown":
        assert web[0]["evidence_text"] == "Source evidence supports this topic."
    if focus == "fetch-failed":
        assert {source["status"] for source in web} == {"fetch_failed"}
    research_calls = [
        call for call in models.calls if call[1] == "a" and call[2] == "ResearchAction"
    ]
    assert len(research_calls) <= 4
    if focus != "unknown":
        # The summary says why research failed, not just that it did.
        assert summary.slots["a"].error.startswith("ModelFailure: failed_research (")
        assert summary.slots["a"].error != "ModelFailure: failed_research"


@pytest.mark.parametrize("mode", ["visual_api", "banned", "new-stat"])
async def test_visual_errors_fallback_to_grounded_real_png(tmp_path, mode):
    behavior = (
        {"visual_api": True}
        if mode == "visual_api"
        else {"overlay": "guaranteed" if mode == "banned" else "999 audits completed"}
    )
    summary = await run(tmp_path, OfflineModels({"a": behavior}), [plan_pair()[0]])
    assert summary.shipped == 1
    record = json.loads((tmp_path / "run/visuals.json").read_text())[0]
    assert record["spec"]["overlay_text"] == DEFAULT_KB_TITLE
    assert record["result"]["fallback_reason"] == "visual_fallback"
    assert Image.open(tmp_path / "run" / record["result"]["path"]).size == (1200, 675)


async def test_pillow_error_exports_spec_json_without_losing_accepted_content(tmp_path):
    def broken(spec, path):
        raise OSError("synthetic rendering failure")

    summary = await run(tmp_path, OfflineModels(), [plan_pair()[0]], renderer=broken)
    assert summary.shipped == 1
    post = json.loads((tmp_path / "run/posts.json").read_text())[0]
    assert post["image"] == "images/slot-a.json"
    assert (
        json.loads((tmp_path / "run" / post["image"]).read_text())["overlay_text"]
        == "1/5 State before calls."
    )


def test_estimate_and_invalid_cli_never_initialize_models_search_or_outputs(tmp_path, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("estimate/startup initialized transport")

    assert (
        main(
            ["--root", str(ROOT), "--output", str(tmp_path / "estimate"), "--estimate"],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["slots"] == 6
    assert not (tmp_path / "estimate").exists()
    assert (
        main(
            ["--root", str(ROOT), "--set", "absent=topic"],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 2
    )
    assert (
        main(
            ["--root", str(ROOT), "--max-tweet-chars", "281"],
            model_factory=forbidden,
            search_factory=forbidden,
        )
        == 2
    )


def test_public_piece_cli_uses_same_graph_and_file_only_type(tmp_path, capsys):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    shutil.copytree(root / "post_types/marketing-post", root / "post_types/case-study")
    path = root / "post_types/case-study/type.yaml"
    shutil.copytree(root / "agents/marketing_writer", root / "agents/case_writer")
    path.write_text(
        path.read_text()
        .replace("name: marketing-post", "name: case-study")
        .replace("writer: marketing_writer", "writer: case_writer")
    )
    models = OfflineModels()
    assert (
        piece_main(
            [
                "--root",
                str(root),
                "--type",
                "case-study",
                "--topic",
                DEFAULT_FOCUS,
                "--output",
                str(tmp_path / "piece"),
            ],
            model_factory=models.factory,
            search_factory=search_factory,
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["shipped"] == 1
    assert summary["slots"]["piece_1"]["outcome"] == "shipped"
    assert (tmp_path / "piece/checkpoint.sqlite").is_file()
    assert json.loads((tmp_path / "piece/posts.json").read_text())[0]["tweets"] == [
        "State before calls."
    ]
    assert any(call[0] == "case_writer" and call[2] == "Draft" for call in models.calls)


async def test_low_score_review_policy_requires_retry_then_drop(tmp_path):
    models = OfflineModels({"a": {"low_scores": True}})
    summary = await run(tmp_path, models, [plan_pair()[0]])
    assert summary.shipped == 0
    assert summary.slots["a"].outcome.value == "dropped_quality"
    assert len([call for call in models.calls if call[2] == "Draft"]) == 2
    assert len([call for call in models.calls if call[2] == "ReviewAssessment"]) == 2


def test_week_cli_set_and_configured_rubric_file_policy(tmp_path, capsys):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    rubric = root / "post_types/tech-thread/rubric.md"
    rubric.write_text("---\ncriteria: [grounding, custom_criterion]\n---\nScore every criterion.\n")
    type_path = root / "post_types/tech-thread/type.yaml"
    type_path.write_text(type_path.read_text().replace("pass_avg: 3.5", "pass_avg: 4.5"))
    models = OfflineModels()
    assert (
        main(
            [
                "--root",
                str(root),
                "--set",
                "thread_2=oracle manipulation",
                "--output",
                str(tmp_path / "run"),
            ],
            model_factory=models.factory,
            search_factory=search_factory,
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["shipped"] == 3
    assert summary["slots"]["thread_2"]["outcome"] == "dropped_quality"
    resolved = json.loads((tmp_path / "run/plan.resolved.json").read_text())
    assert resolved["slots"][1]["focus"] == "oracle manipulation"
    review = next(
        call[3] for call in models.calls if call[1] == "thread_2" and call[2] == "ReviewAssessment"
    )
    assert review["rubric"]["criteria"] == ["grounding", "custom_criterion"]


async def test_topic_history_exclusion_and_exhaustion_are_code_recovery(tmp_path):
    slot = ResolvedSlot(slot_id="a", type="tech-thread")
    first = await run_pipeline(
        ROOT,
        [slot],
        tmp_path / "first",
        model_factory=OfflineModels().factory,
        search_factory=search_factory,
    )
    second = await run_pipeline(
        ROOT,
        [slot],
        tmp_path / "second",
        model_factory=OfflineModels().factory,
        search_factory=search_factory,
    )
    assert first.slots["a"].topic_id != second.slots["a"].topic_id
    all_ids = [
        entry["id"] for entry in json.loads((ROOT / "inputs/kb.json").read_text())["findings"]
    ]
    prior = first.model_copy(deep=True)
    prior.slots = {
        id: contracts.TerminalSlot(outcome="shipped", attempts=1, topic_id=id, reasons=[])
        for id in all_ids
    }
    folder = tmp_path / "complete-history"
    folder.mkdir()
    (folder / "run_summary.json").write_text(prior.model_dump_json())
    third = await run_pipeline(
        ROOT,
        [slot],
        tmp_path / "third",
        model_factory=OfflineModels().factory,
        search_factory=search_factory,
    )
    assert third.shipped == 1
    assert (
        "history_exhausted:a"
        in json.loads((tmp_path / "third/plan.resolved.json").read_text())["warnings"]
    )


async def test_topic_pool_exhaustion_is_slot_local(tmp_path):
    slots = [ResolvedSlot(slot_id=f"marketing_{i}", type="marketing-post") for i in range(6)]
    summary = await run(tmp_path, OfflineModels(), slots)
    assert summary.shipped == 3
    assert summary.slots["marketing_5"].outcome.value == "failed_research"


@pytest.mark.parametrize("slot_id", ["../escape", "C:escape", "CON", "unsafe/name"])
async def test_unsafe_slot_names_fail_before_factory_or_output(tmp_path, slot_id):
    def forbidden(*args):
        pytest.fail("startup initialized a model")

    with pytest.raises(ValueError, match="unsafe"):
        await run_pipeline(
            ROOT,
            [ResolvedSlot(slot_id=slot_id, type="tech-thread", focus="reentrancy")],
            tmp_path / "run",
            model_factory=forbidden,
        )
    assert not (tmp_path / "run").exists()


async def test_snapshots_exclude_env_keys_and_existing_runs_are_preserved(tmp_path, monkeypatch):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    (root / ".env").write_text("OPENAI_API_KEY=synthetic-secret\n")
    (root / "config/.env").write_text("synthetic-secret")
    (root / "agents/research/private.yaml").write_text("api_key: synthetic-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    models = OfflineModels()
    summary = await run_pipeline(
        root,
        [plan_pair()[0]],
        tmp_path / "run",
        model_factory=models.factory,
        search_factory=search_factory,
    )
    assert summary.shipped == 1
    for path in (tmp_path / "run/snapshot").rglob("*"):
        if path.is_file():
            assert "synthetic-secret" not in path.read_text()
    assert b"synthetic-secret" not in (tmp_path / "run/checkpoint.sqlite").read_bytes()
    previous = (tmp_path / "run/posts.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        await run_pipeline(
            root,
            [plan_pair()[0]],
            tmp_path / "run",
            model_factory=models.factory,
            search_factory=search_factory,
        )
    assert (tmp_path / "run/posts.json").read_bytes() == previous


async def test_budget_refusal_in_expensive_writer_leaves_other_lane_shipped(tmp_path):
    root = tmp_path / "root"
    for folder in ["inputs", "agents", "post_types", "skills", "config"]:
        shutil.copytree(ROOT / folder, root / folder)
    shutil.copytree(root / "agents/thread_writer", root / "agents/expensive")
    agent_file = root / "agents/expensive/agent.yaml"
    data = yaml.safe_load(agent_file.read_text())
    data["max_output_tokens"] = 1000000
    agent_file.write_text(yaml.safe_dump(data))
    shutil.copytree(root / "post_types/tech-thread", root / "post_types/expensive-type")
    type_file = root / "post_types/expensive-type/type.yaml"
    type_file.write_text(
        type_file.read_text()
        .replace("name: tech-thread", "name: expensive-type")
        .replace("writer: thread_writer", "writer: expensive")
    )
    slots = [ResolvedSlot(slot_id="a", type="expensive-type", focus=DEFAULT_FOCUS), plan_pair()[1]]
    models = OfflineModels()
    summary = await run_pipeline(
        root, slots, tmp_path / "run", model_factory=models.factory, search_factory=search_factory
    )
    assert summary.slots["a"].outcome.value == "failed_budget"
    assert summary.slots["b"].outcome.value == "shipped"
    assert not any(call[1] == "a" and call[2] == "Draft" for call in models.calls)


async def test_missing_rubric_output_repairs_without_content_retry(tmp_path):
    models = OfflineModels({"a": {"missing_once": True}})
    summary = await run(tmp_path, models, [plan_pair()[0]])
    assert summary.shipped == 1
    assert summary.slots["a"].attempts == 1
    assert len([call for call in models.calls if call[2] == "ReviewAssessment"]) == 2
    assert '"cta"' in models.repairs[0]


def test_fallback_candidates_are_revalidated_for_brand_and_numbers():
    from content_pipeline.images import fallback_spec, overlay_errors
    from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
    from content_pipeline.config import load_type

    source = SourceRecord(
        source_id="WEB-aa",
        kind="web",
        title="999 audits guaranteed",
        evidence_text="Evidence about a root cause.",
    )
    request = contracts.VisualRequest(
        slot_id="a",
        attempt=1,
        draft=Draft(
            slot_id="a", tweets=[Tweet(text="Explain the root cause.", source_ids=["WEB-aa"])]
        ),
        research=ResearchPack(
            slot_id="a", topic_id="WEB-aa", topic_name="999 audits", sources=[source]
        ),
        post_type=load_type(ROOT, "marketing-post"),
    )
    rules, _ = load_brand(ROOT / "inputs/brand-voice.md")
    spec = fallback_spec(request, rules)
    assert spec.overlay_text == "Explain the root cause."
    assert overlay_errors(spec, request, rules) == []
