import hashlib
import json
import os
import sqlite3
from pathlib import Path
from .config import Settings, load_plan, local_file
from .contracts import AdvisorRecord, DraftRecord, ExternalUsage, FinalPiece, LedgerEntry, ResearchRecord, ReviewRecord, RunIdentity, VisualRecord
from .models import UsageRecord


def atomic_json(path, value):
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(path)


def snapshot_hashes(root):
    return {path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


class RunLock:
    """Nonblocking OS lock: process death releases ownership, not billed state."""
    def __init__(self,run_dir,create=False):
        self.path=run_dir/"run.lock"
        if create:
            self.path.write_bytes(b"1")
        self.stream=None

    def __enter__(self):
        self.stream=self.path.open("r+b")
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream=None
            raise ValueError("run already owned") from None
        return self

    def __exit__(self,*args):
        if self.stream:
            if os.name=="nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
            self.stream.close()


def resume_inputs(run_dir: Path, budget_overrides=None):
    """Read-only preflight. Credentials and mutable live policy are never read."""
    identity=RunIdentity.model_validate_json(local_file(run_dir,"run_identity.json").read_text(encoding="utf-8"))
    snapshot=run_dir/"snapshot"
    if snapshot_hashes(snapshot)!=identity.snapshot_files:
        raise ValueError("incompatible snapshot")
    settings=Settings.model_validate_json(local_file(snapshot,"resolved-config.json").read_text(encoding="utf-8"))
    slots=load_plan(snapshot,local_file(snapshot,"inputs/plan.json"))
    budgets=budget_overrides or {}
    if set(budgets)-{"run_token_budget","run_cost_budget_usd"}:
        raise ValueError("resume allows budget increases only")
    current=Settings.model_validate_json(local_file(run_dir,"runtime-settings.json").read_text(encoding="utf-8"))
    for key in ["run_token_budget","run_cost_budget_usd"]:
        value=budgets.get(key,getattr(current,key))
        if value<getattr(current,key): raise ValueError("resume budget cannot decrease")
        setattr(settings,key,value)
    settings=Settings.model_validate(settings.model_dump())
    entries=json.loads(local_file(run_dir,"ledger.json").read_text(encoding="utf-8"))
    for entry in entries.values(): LedgerEntry.model_validate(entry)
    usage=[UsageRecord.model_validate_json(line) for line in local_file(run_dir,"usage.jsonl").read_text(encoding="utf-8").splitlines()]
    if len({entry.call_id for entry in usage})!=len(usage): raise ValueError("duplicate usage ids")
    ExternalUsage.model_validate_json(local_file(run_dir,"external-usage.json").read_text(encoding="utf-8"))
    trail_models={"research_log":ResearchRecord,"drafts":DraftRecord,"review_report":ReviewRecord,"visuals":VisualRecord,"final":FinalPiece,"advisor":AdvisorRecord}
    for name,model in trail_models.items():
        records=json.loads(local_file(run_dir,name+".json").read_text(encoding="utf-8"))
        if not isinstance(records,list) or len({record["record_id"] for record in records})!=len(records):
            raise ValueError("invalid saved trail")
        for record in records:
            typed=model.model_validate(record)
            if name in {"review_report","visuals","advisor"} and typed.input_sha256 is None and not (name=="advisor" and typed.status=="not_enabled"):
                raise ValueError("incompatible saved stage format")
    local_file(run_dir,"run.lock")
    checkpoint=local_file(run_dir,"checkpoint.sqlite")
    with sqlite3.connect(checkpoint.as_uri()+"?mode=ro",uri=True) as connection:
        if not connection.execute("SELECT 1 FROM checkpoints WHERE thread_id=? LIMIT 1",(identity.thread_id,)).fetchone():
            raise ValueError("missing run checkpoint")
    return snapshot,slots,settings,identity
