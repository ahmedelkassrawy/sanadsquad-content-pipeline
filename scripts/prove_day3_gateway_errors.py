"""Archive the real pre-repair client and exercise installed SDK callers offline."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "6dcf26757c65147caa38b1ce2ed6169ade87779d"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if evidence.exists() or evidence.is_relative_to(ROOT):
        raise ValueError("new external evidence directory required")
    evidence.mkdir(parents=True)
    base_ref = subprocess.check_output(["git", "rev-parse", args.base], cwd=ROOT, text=True, encoding="utf-8").strip()
    base = evidence/"base"
    with zipfile.ZipFile(io.BytesIO(subprocess.check_output(["git", "archive", "--format=zip", base_ref], cwd=ROOT))) as archive:
        archive.extractall(base)
    test = ROOT/"tests/test_day3_gateway_errors.py"
    (base/"tests"/test.name).write_text(test.read_text(encoding="utf-8"), encoding="utf-8")
    site = Path(sys.executable).resolve().parents[1]/"Lib/site-packages"
    checks = {}
    for name, folder, expected in [("base-red", base, 1), ("working-green", ROOT, 0)]:
        bootstrap = "import sys;sys.path[:0]=" + repr([str(folder),str(site)]) + ";import content_pipeline,pytest;assert content_pipeline.__file__.startswith(" + repr(str(folder)) + ");raise SystemExit(pytest.main(sys.argv[1:]))"
        command = [sys.executable,"-S","-c",bootstrap,"-q","tests/test_day3_gateway_errors.py","--tb=short","--basetemp",str(evidence/(name+"-tmp"))]
        result = subprocess.run(command, cwd=folder, env={**os.environ,"LANGSMITH_TRACING":"false"}, text=True, encoding="utf-8", capture_output=True)
        output = result.stdout + result.stderr
        (evidence/(name+".txt")).write_text(output, encoding="utf-8")
        checks[name] = {"command":command,"cwd":str(folder),"exit":result.returncode,"expected_exit":expected}
        if name == "base-red":
            checks[name]["intended_failure"] = all(text in output for text in ["7 failed, 7 passed", "This error payload must never be accepted.", "failed_output", "enabled"]) and "ERROR collecting" not in output
    passed = all(c["exit"] == c["expected_exit"] and c.get("intended_failure",True) for c in checks.values())
    report = {"verdict":"PASS" if passed else "FAIL","base":base_ref,"head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True,encoding="utf-8").strip(),"tested_files_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [test, ROOT/"content_pipeline/model_client.py"]},"checks":checks}
    (evidence/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
