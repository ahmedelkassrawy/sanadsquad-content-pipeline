# Advisor numeric-line contract repair

Measured product: `acbe5ec4b32c342914bc0f7d0cfb06b735cc2db3`.
Archived prior product: `591699920601330282efdbdfe5ced65042bf55e1`.
Coordinator's intervening plan-only3ea3efa is preserved.

Each edit now compares affected full lines. Added/removed/modified numeric-bearing
lines reject, including changing surrounding words while omitting the number from
the edit excerpt. Unchanged numeric lines and nonnumeric clarity edits remain allowed.
This is a conservative text guard, not semantic fact checking; every recommendation,
including worded quantities, still needs human review before any manual application.

| Command/check | Exit | Actual result |
| --- | --- | --- |
| `python scripts/prove_day3_advisor_numeric.py red --evidence-dir <external>` | driver0 / pytest1 | 4 intended failures,2 controls passed on archived591 |
| Same driver `green` | driver0 / pytest0 | 6 passed onacbe5ec |
| `python -m pytest -q tests/test_day3_advisor_numeric.py tests/test_day3_delivery.py -k 'advisor_numeric or advisor_suggests or saved_stage' --basetemp <external-preservation> --tb=short` | 0 | 16 passed |
| `git diff --check` | 0 | Clean |

Tests call real run_pipeline with injected structured advisor output, preserving literal
shipped text,250 confirmed tokens,zero uncertain tokens and byte-unchanged source files.
They cover borrowing12 from unrelated fictional front matter, replacing an existing
numeric claim, changing numeric wording, partial-line wording with the number outside
the excerpt, an actual nonnumeric clarity patch and an unchanged numeric no-op.
Rejected advice has failed status/no saved unsafe output/empty patch; content still ships.
The initial excerpt-only local attempt missed the partial-line case; its intended RED
was inspected and the final guard uses full changed lines.

Exact commands/cwd/heads/exits/output:
`C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b\day3-advisor-numeric-final-proof\red.json`
and `green.json`; this report is copied there as `report.md`.
RED uses git archive,Python-S,explicit installed packages and asserted archive imports;
failures are actual unsafe completed advice, not import/async infrastructure errors.

No full suite, live request,credential/.env read,vault/PR or nested agent action.
No schema/plan/manifest change. README/ARCHITECTURE clarify the guard and human review;
verification docs retain latest failed-budget OpenRouter evidence without account usage.
Independent re-review and final release remain coordinator-owned. The separate sample
packager security finding is pending a fresh bounded repair packet. Resource released.
