"""The one reuse-or-refuse rule for saved stage records on resume."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from . import record_ids
from .config import local_file
from .contracts import FinalPiece, ResearchRecord, WeekSnapshot
from .durability import atomic_json
from .images import CARD_SIZE, overlay_errors
from .model_client import ModelFailure
from .models import Contract, Draft, PostType, ResearchPack

MISMATCH = "saved stage input mismatch"


class SavedStageMismatch(ValueError):
    """A saved stage record no longer matches the inputs of the stage that would reuse it."""

    def __init__(self):
        super().__init__(MISMATCH)


class ResearchReplayRefused(ModelFailure):
    """Saved research that cannot be reused; the slot ends failed_research."""

    def __init__(self):
        super().__init__("failed_research")


class ValidationInput(Contract):
    """Everything the validator reads for one draft; the draft record fingerprints it."""

    draft: Draft
    research: ResearchPack
    post_type: PostType
    max_tweet_chars: int


@dataclass(frozen=True)
class SavedStage:
    """A saved record that may be reused; legacy means it was written without a fingerprint."""

    record: object
    legacy: bool = False


def digest(value):
    """Hash a value with the canonical replay JSON encoding."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def request_fingerprint(request):
    """Fingerprint the request a stage was run with."""
    return hashlib.sha256(request.model_dump_json().encode()).hexdigest()


class StageReplay:
    """Answer, for every lane stage, whether a saved record can be reused."""

    def __init__(self, context, trails, plan):
        self.context = context
        self.trails = trails
        self.plan = plan
        self.receipt_lock = asyncio.Lock()

    def _find(self, name, record_id, model):
        raw = next(
            (
                record
                for record in json.loads(self.trails[name].path.read_text(encoding="utf-8"))
                if record["record_id"] == record_id
            ),
            None,
        )

        return None if raw is None else model.model_validate(raw)

    def reuse(self, name, record_id, model, request, *, legacy_ok=False):
        """Return the saved record when its fingerprint matches, else refuse.

        A record without a fingerprint is refused, except where the caller allows legacy
        records written before that stage had one.
        """
        record = self._find(name, record_id, model)

        if record is None:
            return None

        if record.input_sha256 is None and legacy_ok:
            return SavedStage(record, legacy=True)

        if record.input_sha256 != request_fingerprint(request):
            raise SavedStageMismatch()

        return SavedStage(record)

    @staticmethod
    def confirm_audit(saved, audit):
        """Refuse a saved validator audit that the same inputs no longer produce."""
        if not saved.legacy and saved.record.audit != audit:
            raise SavedStageMismatch()

    def research_request_digest(self, request):
        """Hash a research request using its original planned topic."""
        value = request.model_dump(mode="json")

        if request.slot.focus:
            # Compare focused research against the topic in the original snapshot.
            planned = next(slot for slot in self.plan if slot.slot_id == request.slot.slot_id)

            value["slot"]["topic_id"] = planned.topic_id

        return digest(value)

    def _research_receipt(self, request, pack):
        return {
            "request_sha256": self.research_request_digest(request),
            "snapshot_sha256": digest(self.context.identity.snapshot_files),
            "pack_sha256": digest(pack.model_dump(mode="json")),
        }

    def _check_research(self, record_id, request, record):
        receipt_path = self.context.run_dir / "research-replay.json"

        receipts = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        )

        receipt = receipts.get(record_id)

        if (
            record.action != "resolved"
            or record.failure is not None
            or record.pack.slot_id != request.slot.slot_id
            or record.evidence != record.pack.sources
            or not receipt
            or receipt != self._research_receipt(request, record.pack)
        ):
            raise ResearchReplayRefused()

    def research(self, record_id, request):
        """Reuse research only when its identity and replay receipt match."""
        record = self._find("research_log", record_id, ResearchRecord)

        if record is None:
            return None

        if record.slot_id != request.slot.slot_id or record.attempt != request.attempt:
            raise ResearchReplayRefused()

        if record.pack is not None:
            self._check_research(record_id, request, record)

        return record

    def confirm_research(self, slot_id, pack):
        """Refuse a checkpointed research pack that its saved record and receipt do not back.

        The snapshot itself is already checked against the run identity by the resume preflight.
        """
        record_id = record_ids.record_id(slot_id, "research", 1)
        record = self._find("research_log", record_id, ResearchRecord)
        receipt_path = self.context.run_dir / "research-replay.json"

        receipts = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        )

        receipt = receipts.get(record_id)

        if (
            record is None
            or record.pack != pack
            or not receipt
            or receipt.get("pack_sha256") != digest(pack.model_dump(mode="json"))
        ):
            raise SavedStageMismatch()

    def confirm_final(self, piece):
        """Refuse a collected piece whose saved final record is missing or different."""
        if self._find("final", piece.record_id, FinalPiece) != piece:
            raise SavedStageMismatch()

    async def save_research_receipt(self, record_id, request, pack):
        """Persist the request, snapshot and pack fingerprints for replay."""
        receipt = self._research_receipt(request, pack)

        async with self.receipt_lock:
            path = self.context.run_dir / "research-replay.json"
            receipts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

            receipts[record_id] = receipt

            atomic_json(path, receipts)

    def validate_visual(self, saved, request, slot_id, rules):
        """Check that a saved visual's files still match its record."""
        run_dir = self.context.run_dir
        relative = PureWindowsPath(saved.result.path)

        if relative.anchor or ".." in relative.parts:
            raise ValueError("invalid saved image path")

        path = local_file(run_dir, Path(*relative.parts).as_posix())

        stored = local_file(run_dir, path.with_suffix(".json").relative_to(run_dir).as_posix())

        if json.loads(stored.read_text(encoding="utf-8")) != saved.spec.model_dump(
            mode="json"
        ) or overlay_errors(saved.spec, request, rules):
            raise ValueError("saved visual artifact mismatch")

        if saved.result.slot_id != slot_id:
            raise ValueError("saved visual identity mismatch")

        if saved.result.backend in {"pillow_card", "cloudflare_flux"}:
            from PIL import Image

            with Image.open(path) as image:
                if image.format != "PNG" or image.size != CARD_SIZE:
                    raise ValueError("invalid saved card")

                image.verify()


async def completed_summary(graph, config, slots):
    """Return the saved summary of a finished run, or None when nodes remain."""
    saved = await graph.aget_state(config)

    state = WeekSnapshot.model_validate(saved.values)

    if list(state.slots) != [slot.slot_id for slot in slots]:
        raise ValueError("checkpoint plan mismatch")

    if not saved.next and state.summary is not None:
        return state.summary

    if not saved.next:
        raise ValueError("checkpoint has no unfinished nodes")

    return None
