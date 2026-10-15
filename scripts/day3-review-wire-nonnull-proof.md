# Nonnullable optional reviewer reason proof

Product: `229768cf5722eabf475bf330792e6e2dfe7efe10`.
Prior: `d7084e5f1613866e86fbddb7d70ed6beb704a9fa`.

The one-line runtime repair declares flag-only reasons as optional strings with an
empty-string default. `exclude_unset` still preserves omission. Canonical validation
and scoring are unchanged: false empty reasons are allowed, true empty/missing reasons
and explicit null remain rejected. Public schemas and initial2cf proof are untouched.

Reproduce with a fresh external evidence directory:

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day3_review_wire_nonnull.py red --evidence-dir C:\external\wire-nonnull
& .\.venv\Scripts\python.exe scripts/prove_day3_review_wire_nonnull.py green --evidence-dir C:\external\wire-nonnull
```

Archived/import-contained d708 RED pytest exit1: exactly1 failed (actual installed SDK
tool parameter advertises nullable anyOf),2 empty-policy controls passed. Fixed GREEN
pytest exit0:3 passed. Both proof drivers exit0 after count/exit checks. Full focused
command `python -m pytest -q tests/test_day3_review_wire.py
tests/test_day3_review_wire_nonnull.py --tb=short` exits0:13 passed, including all original
10 producer/alias/repair/usage cases. Only the documented parser deprecation warning.

Exact commands/output: external scratch `day3-review-wire-nonnull/red.json` and
`green.json`. Installed SDK HTTP is mocked with synthetic credentials and an empty
temporary credential root; no live calls or actual secrets. Full release, independent
delta review and live controls/sample remain coordinator-owned.
