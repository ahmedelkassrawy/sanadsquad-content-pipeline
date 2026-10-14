"""Copy inspectable sample artifacts; checkpoint/lock/credentials stay private."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from content_pipeline.contracts import AdvisorRecord, DraftRecord, ExportPost, FinalPiece, ResearchRecord, ResolvedPlan, ReviewRecord, RunSummary, VisualRecord
from content_pipeline.models import UsageRecord
from content_pipeline.schemas import export


def package(source,target):
    summary=RunSummary.model_validate_json((source/"run_summary.json").read_text(encoding="utf-8"))
    if summary.shipped!=6: raise ValueError("sample requires six shipped pieces")
    contracts={"research_log":ResearchRecord,"drafts":DraftRecord,"review_report":ReviewRecord,"visuals":VisualRecord,"final":FinalPiece,"advisor":AdvisorRecord,"posts":ExportPost}
    for name,model in contracts.items():
        for record in json.loads((source/(name+".json")).read_text(encoding="utf-8")): model.model_validate(record)
    for line in (source/"usage.jsonl").read_text(encoding="utf-8").splitlines(): UsageRecord.model_validate_json(line)
    ResolvedPlan.model_validate_json((source/"plan.resolved.json").read_text(encoding="utf-8"))
    if target.exists(): raise ValueError("sample destination already exists")
    target.mkdir(parents=True,exist_ok=False)
    for name in [*(name+".json" for name in contracts),"plan.resolved.json","run_summary.json","usage.jsonl","ledger.json","external-usage.json","posts.csv","advice.md","advice.patch","run.log"]:
        shutil.copyfile(source/name,target/name)
    shutil.copytree(source/"images",target/"images")
    shutil.copytree(source/"snapshot",target/"snapshot")
    export(target/"schemas")
    (target/"PACKAGE.md").write_text("Inspectable sample package. SQLite checkpoint, ownership lock and internal run identity/state are excluded; this copy cannot resume. Brand statistics are fictional. Copy the live sample-provenance.json beside this package before claiming live provenance.\n",encoding="utf-8")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    package(args.source,args.output)
