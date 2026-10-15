"""Pin every review finding red on old code and green on the fixed product."""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "d7927e919b03d07a449cc8502bb968e2162c5b18"
GROUPS = {
    "general-1-sdk-transients": "sdk_connection_retry",
    "general-2-truncation": "sdk_truncation or gemini_max_tokens",
    "general-3-malformed": "sdk_malformed",
    "contracts-1-opencode": "opencode_bare",
    "contracts-2-anthropic": "declared_anthropic",
    "contracts-3-invalid-text": "normalized_empty",
    "security-1-credentials-preflight": "gateway_missing or configured_gateway or sdk_initialization_failure",
    "security-2-image-paths": "image_export",
    "security-3-configured-writers": "custom_writer",
}


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

    args = parser.parse_args()
    report_path = ROOT / "scripts/review-fix-proof.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"base": BASE}

    if args.mode == "red":
        archive = subprocess.check_output(["git", "archive", "--format=zip", BASE], cwd=ROOT)
        cwd = Path(tempfile.mkdtemp(prefix="sanadsquad-review-red-"))

        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(cwd)

        (cwd / "tests/test_review_regressions.py").write_bytes(
            (ROOT / "tests/test_review_regressions.py").read_bytes()
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

        report["head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()

    checks = {
        name: run(
            [*prefix, "-q", "tests/test_review_regressions.py", "--tb=short", "-k", selection], cwd
        )
        for name, selection in GROUPS.items()
    }

    report[args.mode] = checks

    if args.mode == "green":
        report["full_suite"] = run([sys.executable, "-m", "pytest", "-q"], ROOT)

        report["pip_check"] = run([sys.executable, "-m", "pip", "check"], ROOT)

        report["schemas"] = run(
            [sys.executable, "-m", "content_pipeline.schemas", "--output", "schemas"], ROOT
        )

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    expected = 1 if args.mode == "red" else 0
    ok = all(check["exit"] == expected for check in checks.values())

    if args.mode == "green":
        ok = ok and all(report[key]["exit"] == 0 for key in ["full_suite", "pip_check", "schemas"])

    print(
        json.dumps(
            {
                "mode": args.mode,
                "verdict": "PASS" if ok else "FAIL",
                "exits": {key: value["exit"] for key, value in checks.items()},
                "artifact": str(report_path),
            }
        )
    )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
