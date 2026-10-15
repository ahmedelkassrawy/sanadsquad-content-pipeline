"""Record rerunnable base-red, required-guard bypass red and head-green proof."""

import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "438120aa42ff9ec1904435a71485333ba0eedeba"


def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    return {
        "command": command,
        "cwd": str(cwd),
        "exit": result.returncode,
        "output": result.stdout + result.stderr,
    }


def main():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", BASE], cwd=ROOT)
    base_dir = Path(tempfile.mkdtemp(prefix="sanadsquad-base-red-"))
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        bundle.extractall(base_dir)
    # -S prevents an editable head installation from masking the absent base capability.
    baseline = run(
        [sys.executable, "-S", "-m", "content_pipeline.schemas", "--output", "schemas"], base_dir
    )
    mutant = "import pytest; import content_pipeline.validation as v; v.check_citations=lambda draft,pack: []; raise SystemExit(pytest.main(['-q','tests/test_foundation.py::test_citation_acceptance_guard']))"
    bypass = run([sys.executable, "-c", mutant], ROOT)
    green = run([sys.executable, "-m", "pytest", "-q"], ROOT)
    schemas = run([sys.executable, "-m", "content_pipeline.schemas", "--output", "schemas"], ROOT)
    report = {
        "head": head,
        "base": BASE,
        "base_red": baseline,
        "guard_bypass_red": bypass,
        "head_green": green,
        "schema_export": schemas,
        "limitations": [
            "No live model/search calls, provider adapter integration unverified.",
            "No graph, complete agents, week/piece CLIs, live reviewer grounding, images, advisor or resume.",
            "KB entries pending Kassra review; brand statistics fictional demo fixtures.",
            "twitter-text-parser requires setuptools<81 and emits upstream pkg_resources deprecation warning.",
            "LLM budget is an estimated pre-call guard; search/image USD excluded; dispatched errors retain uncertain reservation.",
        ],
        "pending_stages": [
            "independent general/security/API review",
            "coordinator release verification",
            "PR/merge approval",
            "later pipeline implementation and live sample",
        ],
    }
    (ROOT / "scripts/verification-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    valid = (
        baseline["exit"] == 1
        and "No module named 'content_pipeline'" in baseline["output"]
        and bypass["exit"] == 1
        and "test_citation_acceptance_guard" in bypass["output"]
        and green["exit"] == 0
        and schemas["exit"] == 0
    )
    print(
        json.dumps(
            {
                "verdict": "PASS" if valid else "FAIL",
                "head": head,
                "exits": {
                    "base": baseline["exit"],
                    "bypass": bypass["exit"],
                    "head": green["exit"],
                    "schemas": schemas["exit"],
                },
                "artifact": "scripts/verification-report.json",
            }
        )
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
