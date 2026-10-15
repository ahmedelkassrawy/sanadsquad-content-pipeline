"""Focused old-package red and fixed-package green for empty content."""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "773a036be8165d79efc17049b1596ee9eaf09fd2"


def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)

    return {
        "command": command,
        "cwd": str(cwd),
        "exit": result.returncode,
        "output": result.stdout + result.stderr,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("mode", choices=["red", "green"])

    mode = parser.parse_args().mode
    report_path = ROOT / "scripts/empty-content-proof.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"base": BASE}

    if mode == "red":
        cwd = Path(tempfile.mkdtemp(prefix="sanadsquad-empty-red-"))
        archive = subprocess.check_output(["git", "archive", "--format=zip", BASE], cwd=ROOT)

        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(cwd)

        (cwd / "tests/test_empty_content.py").write_bytes(
            (ROOT / "tests/test_empty_content.py").read_bytes()
        )

        site = Path(sys.executable).resolve().parents[1] / "Lib/site-packages"

        code = (
            "import sys; sys.path[:0]="
            + repr([str(cwd), str(site)])
            + "; import content_pipeline,pytest; assert content_pipeline.__file__.startswith("
            + repr(str(cwd))
            + "), content_pipeline.__file__; raise SystemExit(pytest.main(sys.argv[1:]))"
        )

        command = [
            sys.executable,
            "-S",
            "-c",
            code,
            "-q",
            "tests/test_empty_content.py",
            "--tb=short",
        ]
    else:
        cwd = ROOT

        report["head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()

        command = [sys.executable, "-m", "pytest", "-q", "tests/test_empty_content.py"]

    report[mode] = run(command, cwd)

    if mode == "green":
        report["full_suite"] = run([sys.executable, "-m", "pytest", "-q"], ROOT)

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    expected = 1 if mode == "red" else 0

    passed = report[mode]["exit"] == expected and (
        mode == "red" or report["full_suite"]["exit"] == 0
    )

    print(
        json.dumps(
            {
                "mode": mode,
                "exit": report[mode]["exit"],
                "verdict": "PASS" if passed else "FAIL",
                "artifact": str(report_path),
            }
        )
    )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
