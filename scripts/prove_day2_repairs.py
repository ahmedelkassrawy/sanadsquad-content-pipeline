"""Focused per-finding red/green proof; all outputs stay external."""

import argparse
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "f4c1fd07cd2cd4e3faadb5e44d80597b83300c3d"
GROUPS = {
    "general-topic-reservation": "proposal or exhaustion or reserved_proposals",
    "contract-angles-preflight": "angles or missing_angle_file",
    "contract-startup-warning": "normal_cli_warns",
}


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("mode", choices=["red", "green"])
    parser.add_argument("--evidence-dir", required=True, type=Path)

    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()

    if evidence.is_relative_to(ROOT):
        raise ValueError("use external evidence")

    evidence.mkdir(parents=True, exist_ok=True)

    report_path = evidence / (args.mode + ".json")

    if report_path.exists():
        raise ValueError("evidence already exists")

    env = {**os.environ, "LANGSMITH_TRACING": "false"}

    def run(command, cwd):
        result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)

        return {
            "command": command,
            "cwd": str(cwd),
            "exit": result.returncode,
            "output": result.stdout + result.stderr,
        }

    if args.mode == "red":
        cwd = evidence / "base"

        cwd.mkdir(exist_ok=False)

        archive = subprocess.check_output(["git", "archive", "--format=zip", BASE], cwd=ROOT)

        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(cwd)

        (cwd / "tests/test_day2_repairs.py").write_bytes(
            (ROOT / "tests/test_day2_repairs.py").read_bytes()
        )

        site = Path(sys.executable).resolve().parents[1] / "Lib/site-packages"

        bootstrap = (
            "import sys; sys.path[:0]="
            + repr([str(cwd), str(site)])
            + "; import content_pipeline,pytest; assert content_pipeline.__file__.startswith("
            + repr(str(cwd))
            + "), content_pipeline.__file__; raise SystemExit(pytest.main(sys.argv[1:]))"
        )

        prefix = [sys.executable, "-S", "-c", bootstrap]
    else:
        cwd = ROOT
        prefix = [sys.executable, "-m", "pytest"]

    checks = {
        name: run(
            [
                *prefix,
                "-q",
                "tests/test_day2_repairs.py",
                "--tb=short",
                "-k",
                selection,
                "--basetemp",
                str(evidence / (args.mode + "-" + name)),
            ],
            cwd,
        )
        for name, selection in GROUPS.items()
    }

    if args.mode == "green":
        checks["preservation"] = run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_foundation.py::test_kb_alias_and_batch_assignment",
                "tests/test_pipeline.py",
                "-k",
                "kb_alias_and_batch_assignment or topic_history or topic_pool or week_cli_set or public_piece or estimate_and_invalid or public_six",
                "--basetemp",
                str(evidence / "preservation"),
            ],
            ROOT,
        )

        checks["schemas"] = run(
            [
                sys.executable,
                "-m",
                "content_pipeline.schemas",
                "--output",
                str(evidence / "schemas"),
            ],
            ROOT,
        )

    expected = 1 if args.mode == "red" else 0

    passed = all(
        result["exit"] == expected for name, result in checks.items() if name in GROUPS
    ) and all(result["exit"] == 0 for name, result in checks.items() if name not in GROUPS)

    report = {
        "verdict": "PASS" if passed else "FAIL",
        "mode": args.mode,
        "base": BASE,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "checks": checks,
        "full_suite": "coordinator-owned; not rerun in repair wave",
    }

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "mode": args.mode,
                "exits": {name: result["exit"] for name, result in checks.items()},
                "report": str(report_path),
            }
        )
    )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
