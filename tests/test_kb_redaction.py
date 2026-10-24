"""KB names never reach writers or pass draft validation."""

import json
from pathlib import Path

import pytest

from content_pipeline.config import load_brand, load_type
from content_pipeline.models import Draft, ResearchPack, ResolvedSlot, SourceRecord, Tweet
from content_pipeline.redaction import (
    kb_protocol_name_violations,
    load_redacted_names,
    redact_kb_text,
    redact_writer_material,
)
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


def test_redacts_identifier_and_compact_name_variants_but_not_longer_word():
    text = "swig_authority swigAta SwigWallet swigged privacy-cash privacycash"

    assert redact_kb_text(text) == (
        "protocol_authority protocolAta protocolWallet swigged the protocol the protocol"
    )


def test_kb_source_redacts_identifier_program_ids_and_github_paths():
    source = KnowledgeBase(ROOT / "inputs/kb.json").source("SS-03699")

    assert "swig" not in source.evidence_text.casefold()
    assert "swig_authority" not in source.evidence_text
    assert "github.com/" not in source.evidence_text.casefold()
    assert "swigDk8JezhiAVde8k6NMwxpZfgGm2NNuMe1KYCmUjP" not in source.evidence_text
    assert "<program id>" in source.evidence_text


def test_writer_visible_pack_scrubs_every_field_when_it_includes_kb_evidence():
    pack = ResearchPack(
        slot_id="slot",
        topic_id="SS-03699",
        topic_name="SwigWallet review",
        sources=[
            SourceRecord(
                source_id="SS-03699",
                kind="kb",
                title="Swig finding",
                evidence_text="swig_authority uses swigDk8JezhiAVde8k6NMwxpZfgGm2NNuMe1KYCmUjP.",
            ),
            SourceRecord(
                source_id="STAT-01",
                kind="stat",
                title="Swig",
                evidence_text="SwigWallet coverage.",
            ),
            SourceRecord(
                source_id="WEB-aaaaaaaaaaaaaaaa",
                kind="web",
                url="https://example.com/privacy-cash/SwigWallet",
                title="Swig analysis",
                evidence_text="SwigWallet details.",
            ),
        ],
    )

    redacted, feedback, previous = redact_writer_material(
        pack,
        ["kb_protocol_name:Swig", "retry swig_authority"],
        _draft("Retry SwigWallet at https://example.com/privacy-cash", count=1),
        ROOT,
    )

    writer_text = redacted.model_dump_json() + "\n" + "\n".join(feedback)
    assert "swig" not in writer_text.casefold()
    assert "privacycash" not in writer_text.casefold().replace("-", "")
    assert "<program id>" in writer_text
    assert previous is not None
    assert "swig" not in previous.model_dump_json().casefold()


def test_validation_rejects_identifier_and_url_slug_protocol_variants():
    rules = load_brand(ROOT / "inputs/brand-voice.md")[0]
    spec = load_type(ROOT, "marketing-post")

    identifier = validate(_draft("`swig_authority`"), _pack(), spec, rules)
    slugs = [
        validate(_draft(url), _pack(), spec, rules)
        for url in (
            "https://example.com/privacy-cash",
            "https://example.com/privacycash",
            "https://example.com/privacy%2Dcash",
        )
    ]

    assert "kb_protocol_name:Swig" in identifier.violations
    assert all("kb_protocol_name:Privacy Cash" in result.violations for result in slugs)


@pytest.mark.asyncio
async def test_retry_writer_payload_redacts_feedback_and_previous_draft(tmp_path):
    from test_pipeline_end_to_end import OfflineModels, run

    models = OfflineModels({"a": {"writer_text": "`swig_authority`"}})
    await run(
        tmp_path,
        models,
        [ResolvedSlot(slot_id="a", type="tech-thread", topic_id="SS-03699")],
    )

    payloads = [call[3] for call in models.calls if call[2] == "Draft"]
    assert len(payloads) == 2
    for payload in payloads:
        assert "swig" not in json.dumps(payload).casefold()
    assert "protocol_authority" in payloads[1]["previous_draft"]["tweets"][0]["text"]
    assert any("the protocol" in note for note in payloads[1]["feedback"])


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


def test_validation_uses_the_run_snapshot_redaction_files(tmp_path):
    root = tmp_path / "snapshot"
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "kb.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    (inputs / "redacted-names.txt").write_text("Snapshot Name\n", encoding="utf-8")

    result = validate(
        _draft("Snapshot Name is affected."),
        _pack(),
        load_type(ROOT, "marketing-post"),
        load_brand(ROOT / "inputs/brand-voice.md")[0],
        ai_policy_root=root,
    )

    assert "kb_protocol_name:Snapshot Name" in result.violations


def test_framework_and_client_names_are_redacted_from_the_squads_finding():
    source = KnowledgeBase(ROOT / "inputs/kb.json").source("SS-03804")

    assert "squads" not in source.evidence_text.casefold()
    assert "metadao" not in source.evidence_text.casefold()
    assert "squads" not in source.title.casefold()


def test_validation_rejects_the_squads_framework_name_in_a_draft():
    result = validate(
        _draft("The ProposalActivate instruction in Squads relies on the initiator."),
        _pack(),
        load_type(ROOT, "tech-thread"),
        load_brand(ROOT / "inputs/brand-voice.md")[0],
    )

    assert "kb_protocol_name:Squads" in result.violations


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


def test_url_slug_flags_only_the_protocol_it_names():
    violations = kb_protocol_name_violations("See https://x.org/privacy-cash/post today.")

    assert violations == ["kb_protocol_name:Privacy Cash"]


def test_every_stored_protocol_name_is_redacted_and_rejected_exactly_as_stored():
    names = [
        finding["protocol"]
        for finding in json.loads((ROOT / "inputs/kb.json").read_text(encoding="utf-8"))["findings"]
    ]

    for name in names:
        assert name.casefold() not in redact_kb_text(name).casefold()
        assert kb_protocol_name_violations(name)


def test_kb_redaction_preserves_unrelated_web_urls_and_hashes():
    pack = ResearchPack(
        slot_id="slot",
        topic_id="SS-03699",
        topic_name="Swig finding",
        sources=[
            SourceRecord(source_id="SS-03699", kind="kb", title="Finding", evidence_text="Facts."),
            SourceRecord(
                source_id="WEB-aaaaaaaa",
                kind="web",
                url="https://github.com/acme/security-advisories/abcdefabcdefabcdefabcdefabcdefabcdefabcd",
                title="Independent advisory",
                evidence_text="Commit abcdefabcdefabcdefabcdefabcdefabcdefabcd is unrelated.",
            ),
        ],
    )

    redacted, _, _ = redact_writer_material(pack, [], None, ROOT)
    web = next(source for source in redacted.sources if source.kind == "web")

    assert web.url == pack.sources[1].url
    assert web.evidence_text == pack.sources[1].evidence_text
