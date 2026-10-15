"""Isolated prior-head RED and focused GREEN, with external evidence only."""

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "3ed474e5ae58f992e21114f79f50689fc081d2cb"


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("mode", choices=["red", "green"])
    parser.add_argument("--evidence-dir", type=Path, required=True)

    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()

    if evidence.is_relative_to(ROOT):
        raise ValueError("external evidence required")

    evidence.mkdir(parents=True, exist_ok=True)

    report = evidence / (args.mode + ".json")

    if report.exists():
        raise ValueError("evidence already exists")

    if args.mode == "red":
        cwd = evidence / "base"

        cwd.mkdir()

        with zipfile.ZipFile(
            io.BytesIO(subprocess.check_output(["git", "archive", "--format=zip", BASE], cwd=ROOT))
        ) as archive:
            archive.extractall(cwd)

        (cwd / "tests/test_day2_angle_routing.py").write_bytes(
            (ROOT / "tests/test_day2_angle_routing.py").read_bytes()
        )

        site = Path(sys.executable).resolve().parents[1] / "Lib/site-packages"

        bootstrap = (
            "import sys; sys.path[:0]="
            + repr([str(cwd), str(site)])
            + "; import content_pipeline,pytest; assert content_pipeline.__file__.startswith("
            + repr(str(cwd))
            + "),content_pipeline.__file__; raise SystemExit(pytest.main(sys.argv[1:]))"
        )

        prefix = [sys.executable, "-S", "-c", bootstrap]
    else:
        cwd = ROOT
        prefix = [sys.executable, "-m", "pytest"]

    command = [
        *prefix,
        "-q",
        "tests/test_day2_angle_routing.py",
        "--tb=short",
        "--basetemp",
        str(evidence / (args.mode + "-tmp")),
    ]

    result = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, "LANGSMITH_TRACING": "false"},
        capture_output=True,
        text=True,
    )

    expected = 1 if args.mode == "red" else 0
    passed = result.returncode == expected

    data = {
        "verdict": "PASS" if passed else "FAIL",
        "base": BASE,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "command": command,
        "cwd": str(cwd),
        "exit": result.returncode,
        "output": result.stdout + result.stderr,
    }

    report.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(json.dumps({key: data[key] for key in ["verdict", "head", "exit"]}))

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
