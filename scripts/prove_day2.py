"""Offline proof runner: writes only to a new external evidence directory."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "27c40d29e7f13f6fd70f985f22a4afdbb100ed81"


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--evidence-dir", required=True, type=Path)

    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()

    if evidence.is_relative_to(ROOT):
        raise ValueError("use an external evidence directory")

    evidence.mkdir(parents=True, exist_ok=False)

    env = {**os.environ, "LANGSMITH_TRACING": "false"}

    def run(command, cwd=ROOT):
        result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)

        return {
            "command": command,
            "cwd": str(cwd),
            "exit": result.returncode,
            "output": result.stdout + result.stderr,
        }

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    base = evidence / "base"

    base.mkdir()

    archive = subprocess.check_output(["git", "archive", "--format=zip", BASE], cwd=ROOT)

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        bundle.extractall(base)

    site = Path(sys.executable).resolve().parents[1] / "Lib/site-packages"

    bootstrap = (
        "import sys; sys.path[:0]="
        + repr([str(base), str(site)])
        + "; import content_pipeline; assert content_pipeline.__file__.startswith("
        + repr(str(base))
        + "), content_pipeline.__file__; from content_pipeline.pipeline import run_pipeline"
    )

    checks = {
        "base_python": run([sys.executable, "-S", "-c", bootstrap], base),
        "base_week": run([sys.executable, "-S", str(base / "run_week.py"), "--estimate"], base),
        "base_piece": run(
            [
                sys.executable,
                "-S",
                str(base / "run_piece.py"),
                "--type",
                "tech-thread",
                "--topic",
                "reentrancy",
                "--estimate",
            ],
            base,
        ),
    }

    validation_mutant = (
        "import pytest,content_pipeline.pipeline as p; original=p.validate\ndef bypass(*args,**kwargs):\n result=original(*args,**kwargs); result.accepted=True; result.violations=[]; return result\np.validate=bypass\nraise SystemExit(pytest.main(['-q','tests/test_pipeline.py::test_validator_guard_never_reaches_reviewer_or_visual','--basetemp',"
        + repr(str(evidence / "validation-mutant"))
        + "]))"
    )

    review_mutant = (
        "import pytest,content_pipeline.agents as a; from content_pipeline.models import Review\ndef bypass(request,assessment):\n return Review(slot_id=request.slot_id,scores=assessment.scores,reasons=[],hard_fail=[],accepted=True)\na.score_review=bypass\nraise SystemExit(pytest.main(['-q','tests/test_pipeline.py::test_reviewer_guard_rejects_unsupported_claim','--basetemp',"
        + repr(str(evidence / "review-mutant"))
        + "]))"
    )

    checks["validator_bypass"] = run([sys.executable, "-c", validation_mutant])

    checks["reviewer_bypass"] = run([sys.executable, "-c", review_mutant])

    checks["full_suite"] = run(
        [sys.executable, "-m", "pytest", "-q", "--basetemp", str(evidence / "pytest")]
    )

    checks["pip_check"] = run([sys.executable, "-m", "pip", "check"])

    checks["schemas"] = run(
        [sys.executable, "-m", "content_pipeline.schemas", "--output", str(evidence / "schemas")]
    )

    schema_equal = {
        path.name: json.loads(path.read_text())
        == json.loads((ROOT / "schemas" / path.name).read_text())
        for path in (evidence / "schemas").glob("*.json")
    }

    schema_equal["file_set_equal"] = {
        path.name for path in (evidence / "schemas").glob("*.json")
    } == {path.name for path in (ROOT / "schemas").glob("*.json")}

    checks["week_help"] = run([sys.executable, "run_week.py", "--help"])

    checks["piece_help"] = run([sys.executable, "run_piece.py", "--help"])

    checks["estimate"] = run([sys.executable, "run_week.py", "--estimate"])

    checks["offline_smoke"] = run(
        [
            sys.executable,
            "scripts/offline_pipeline_smoke.py",
            "--output",
            str(evidence / "offline-run"),
        ]
    )

    checks["diff_check"] = run(["git", "diff", "--check", BASE])

    for name in ["slot-thread_1.png", "slot-marketing_1.png"]:
        shutil.copyfile(evidence / "offline-run/images" / name, evidence / name)

    expected = {
        "base_python": 1,
        "base_week": 2,
        "base_piece": 2,
        "validator_bypass": 1,
        "reviewer_bypass": 1,
    }

    passed = (
        all(result["exit"] == expected.get(name, 0) for name, result in checks.items())
        and bool(schema_equal)
        and all(schema_equal.values())
        and "No module named 'content_pipeline.pipeline'" in checks["base_python"]["output"]
    )

    report = {
        "verdict": "PASS" if passed else "FAIL",
        "head": head,
        "base": BASE,
        "checks": checks,
        "schemas_equal": schema_equal,
        "artifact_run": str(evidence / "offline-run"),
        "provenance": "Offline injected scripted models/evidence; no live or semantic grounding proof.",
        "pending": [
            "independent general/contract/security review",
            "coordinator release verification",
            "Day 3 live sample/grounding/resume/advisor/optional Cloudflare",
            "KB owner review",
            "PR/merge/deployment authorization",
        ],
    }

    (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "head": head,
                "exits": {name: result["exit"] for name, result in checks.items()},
                "report": str(evidence / "report.json"),
            }
        )
    )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
