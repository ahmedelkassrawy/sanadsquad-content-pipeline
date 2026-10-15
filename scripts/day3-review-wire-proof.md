# Reviewer wire representation proof

Product head: `d26d9d6201ae0cc3be4e44777c17abe23781990c`.
Prior product: `2cf9737dae0fcd98c939d782ad5966878de19da0`.

Run from the project with its pinned venv, supplying a fresh directory outside the
repository (the script refuses existing reports):

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day3_review_wire.py red --evidence-dir C:\external\review-wire
& .\.venv\Scripts\python.exe scripts/prove_day3_review_wire.py green --evidence-dir C:\external\review-wire
```

The RED archive imports only the old package plus installed dependencies: pytest
exit1, four intended schema-sensitive public Reviewer failures and six rejection
controls passing. GREEN pytest exit0, ten cases passing. Both proof commands exit0
only after checking those counts; JSON reports retain exact commands and output.
The installed OpenAI SDK sends real tool parameters through mocked HTTP; synthetic
credentials are confined to an empty temporary credential root. No live API calls.

Preservation: `python -m pytest -q tests/test_pipeline.py tests/test_day3_delivery.py
tests/test_review_regressions.py --tb=short` exit0,97 passed. Exported56 public schemas
match tracked JSON byte-for-byte. Public dictionary contracts, approval scoring,
thresholds and repair budgets are unchanged. False-flag reasons may be omitted;
true-flag reasons remain mandatory, and extra string reasons remain valid.

Recorded artifacts: `C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b\day3-review-wire-final`.
Independent review, full release suite and configured live controls/sample remain
coordinator-owned; offline producer behavior does not establish live grounding quality.
