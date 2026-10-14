"""Offline behavior proof: archived base, current head and guard mutants."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BASE="bc9cc75dbd78806ace0e6c4e953cc91e1e06e45b"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--evidence-dir",required=True,type=Path)
    args=parser.parse_args()
    evidence=args.evidence_dir.resolve()
    if evidence.is_relative_to(ROOT) or evidence.exists(): raise ValueError("new external evidence directory required")
    evidence.mkdir(parents=True)
    env={**os.environ,"LANGSMITH_TRACING":"false"}
    checks={}
    site=Path(sys.executable).resolve().parents[1]/"Lib/site-packages"
    def archive(ref,name):
        folder=evidence/name
        folder.mkdir()
        with zipfile.ZipFile(io.BytesIO(subprocess.check_output(["git","archive","--format=zip",ref],cwd=ROOT))) as bundle: bundle.extractall(folder)
        return folder
    def run(name,command,cwd,expected):
        result=subprocess.run(command,cwd=cwd,env=env,text=True,capture_output=True)
        checks[name]={"command":command,"cwd":str(cwd),"exit":result.returncode,"expected":expected,"output":result.stdout+result.stderr}
        return result
    def pytest_prefix(folder,extra=""):
        bootstrap="import sys; sys.path[:0]="+repr([str(folder),str(site)])+"; import content_pipeline,pytest; assert content_pipeline.__file__.startswith("+repr(str(folder))+"),content_pipeline.__file__; "+extra+"raise SystemExit(pytest.main(sys.argv[1:]))"
        return [sys.executable,"-S","-c",bootstrap]
    base=archive(BASE,"base")
    for name in ["test_day3_delivery.py","test_day3_gemini.py"]: (base/"tests"/name).write_bytes((ROOT/"tests"/name).read_bytes())
    for name,file,selection in [("base-resume","test_day3_delivery.py","resume_actual_graph or completed_resume"),("base-advisor","test_day3_delivery.py","advisor_suggests"),("base-gemini","test_day3_gemini.py","")]:
        result=run(name,[*pytest_prefix(base),"-q","tests/"+file,"-k",selection,"--tb=short","--basetemp",str(evidence/name)],base,1)
        if "unexpected keyword argument 'interrupt_after'" not in result.stdout and name=="base-resume": raise ValueError("base resume did not fail for capability absence")
    result=run("base-cli",[sys.executable,"-S","-c","import sys;sys.path[:0]="+repr([str(base),str(site)])+";from content_pipeline.cli import main;raise SystemExit(main(['--resume','missing']))"],base,2)
    if "unrecognized arguments: --resume" not in result.stderr: raise ValueError("wrong CLI base failure")
    run("head-focused",[sys.executable,"-m","pytest","-q","tests/test_day3_delivery.py","tests/test_day3_gemini.py","tests/test_day3_live_gate.py","--tb=short","--basetemp",str(evidence/"head-tmp")],ROOT,0)
    run("head-ungated-live",[sys.executable,"-m","content_pipeline.live_proof","--output",str(evidence/"not-created")],ROOT,0)
    if (evidence/"not-created").exists(): raise ValueError("ungated live created output")
    head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    mutants=archive(head,"mutants")
    path=mutants/"content_pipeline/pipeline.py"
    original=path.read_text(encoding="utf-8")
    path.write_text(original.replace("None if resume is not None else ",""),encoding="utf-8")
    run("resume-bypass",[*pytest_prefix(mutants),"-q","tests/test_day3_delivery.py","-k","resume_actual_graph and lane:write","--tb=short","--basetemp",str(evidence/"resume-bypass-tmp")],mutants,1)
    path.write_text(original,encoding="utf-8")
    extra="import content_pipeline.advisor as a,content_pipeline.pipeline as p; a.advice_patch=p.advice_patch=lambda *args:'bypassed\\n'; "
    run("advisor-guard-bypass",[*pytest_prefix(mutants,extra),"-q","tests/test_day3_delivery.py","-k","advisor_suggests and escape","--tb=short","--basetemp",str(evidence/"advisor-bypass-tmp")],mutants,1)
    run("schemas",[sys.executable,"-m","content_pipeline.schemas","--output",str(evidence/"schemas")],ROOT,0)
    actual={p.name:json.loads(p.read_text()) for p in (evidence/"schemas").glob("*.json")}
    committed={p.name:json.loads(p.read_text()) for p in (ROOT/"schemas").glob("*.json")}
    passed=all(check["exit"]==check["expected"] for check in checks.values()) and actual==committed
    (evidence/"report.json").write_text(json.dumps({"verdict":"PASS" if passed else "FAIL","base":BASE,"head":head,"schema_count":len(actual),"checks":checks},indent=2),encoding="utf-8")
    print(json.dumps({"verdict":"PASS" if passed else "FAIL","head":head,"exits":{name:check["exit"] for name,check in checks.items()},"schemas":len(actual)}))
    return 0 if passed else 1


if __name__=="__main__": raise SystemExit(main())
