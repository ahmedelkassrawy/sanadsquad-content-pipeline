# Day 2 Windows slot collision repair

Measured product: `e755c5cdb6e020c0374fc3ca353639e6c3126817`.
Prior archived head: `41cdff9fe807a7ded27c6375e979fcb9961e328b`.
Startup now compares slot IDs with casefold before output creation or factory initialization.
The guard preserves original IDs in exported contracts.

| Check | Exit | Result |
| --- | --- | --- |
| Archived prior Python/CLI collision refusal plus distinct-ID control | 1 | 2 intended acceptance failures, 1 passed |
| Fixed Python/CLI collision refusal plus distinct-ID control | 0 | 3 passed |
| All 20 earlier repairs and five existing unsafe/startup controls | 0 | 25 passed |

Both RED/GREEN proof drivers exited 0. RED uses `git archive`, `-S`, explicit archive
and installed site-package paths, and asserts imported product containment. Its
failures are `case-colliding slots were accepted` and CLI accepted status0 versus
required2; no async infrastructure failure. GREEN confirms zero factory/model calls
and absent output on refusal. The positive control inspects actual separate PNG/spec
files, PNG format/1200x675 dimensions, and spec consistency with each visual trail.

Reproduce with a fresh external evidence directory:

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day2_slot_collision.py red --evidence-dir <external-directory>
& .\.venv\Scripts\python.exe scripts/prove_day2_slot_collision.py green --evidence-dir <external-directory>
& .\.venv\Scripts\python.exe -m pytest -q tests/test_day2_repairs.py tests/test_pipeline.py -k 'test_day2_repairs or unsafe_slot or estimate_and_invalid' --basetemp <external-preservation-directory> --tb=short
```

External evidence: `C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b\day2-slot-collision-proof`.
`red.json` and `green.json` retain exact commands, numeric exits and captured output;
this report is copied there as `report.md`. Original127 cases and prior proof history
are unchanged. No contracts/pins/schemas changed, no live calls or model spending.
Full suite and independent security review remain coordinator-owned. Existing
twitter-text/pkg_resources warning remains. Test resource released after focused checks.
