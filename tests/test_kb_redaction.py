"""KB names never reach writers or pass draft validation."""

import json
from pathlib import Path

from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, SourceRecord, Tweet
from content_pipeline.redaction import load_redacted_names, redact_kb_text
from content_pipeline.validation import validate
from skills.search_kb import KnowledgeBase


ROOT = Path(__file__).resolve().parents[1]


def _draft(text: str, count: int = 1) -> Draft:
    return Draft(slot_id="slot", tweets=[Tweet(text=text) for _ in range(count)])


def _pack() -> ResearchPack:
    return ResearchPack(
        slot_id="slot",
        topic_id="SS-03756",
        topic_name="A finding",
        sources=[
            SourceRecord(source_id="SS-03756", kind="kb", title="Finding", evidence_text="Facts.")
        ],
    )


def test_kb_source_redacts_protocol_from_writer_visible_fields():
    source = KnowledgeBase(ROOT / "inputs/kb.json").source("SS-03756")

    assert source.url is None
    assert "Light Protocol" not in source.title
    assert "Light Protocol" not in source.evidence_text
    assert "Batched Merkle Trees" not in source.evidence_text


def test_redacts_protocol_name_inside_description_case_insensitively():
    text = "LIGHT protocol had a finding. Another light protocol detail follows."

    assert (
        redact_kb_text(text) == "the protocol had a finding. Another the protocol detail follows."
    )


def test_redacts_parenthesis_head_variant_but_not_generic_component_word():
    text = "Light Protocol has a bug. A protocol is not a project name."

    assert redact_kb_text(text) == "the protocol has a bug. A protocol is not a project name."


def test_validation_rejects_kb_protocol_name_for_tech_thread():
    result = validate(
        _draft("Light Protocol has a bug.", count=5),
        _pack(),
        load_type(ROOT, "tech-thread"),
        load_brand(ROOT / "inputs/brand-voice.md")[0],
    )

    assert "kb_protocol_name:Light Protocol" in result.violations


def test_validation_rejects_kb_protocol_name_for_marketing_post():
    result = validate(
        _draft("Light Protocol has a bug."),
        _pack(),
        load_type(ROOT, "marketing-post"),
        load_brand(ROOT / "inputs/brand-voice.md")[0],
    )

    assert "kb_protocol_name:Light Protocol" in result.violations


def test_extra_redacted_names_file_is_honoured(tmp_path):
    kb_path = tmp_path / "kb.json"
    extra_path = tmp_path / "redacted-names.txt"
    kb_path.write_text(json.dumps({"findings": [{"protocol": "Known Project"}]}), encoding="utf-8")
    extra_path.write_text("# An operator addition\nPrivate Project\n", encoding="utf-8")

    assert load_redacted_names(kb_path, extra_path) == ("Private Project", "Known Project")


def test_kb_json_has_no_replacement_characters():
    data = json.loads((ROOT / "inputs/kb.json").read_text(encoding="utf-8"))

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    assert all("\ufffd" not in value for value in strings(data))
