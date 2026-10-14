# Day 2 configured angle routing repair

Measured product: `5d8c21e19d01bd4eb57d334b5a57885b6224efe7`.
Archived prior head: `3ed474e5ae58f992e21114f79f50689fc081d2cb`.
The assigned KB shortcut now requires configured `topic_source == kb`; angle IDs
retain their accepted safe-string namespace and route to the angles producer.

| Check | Exit | Result |
| --- | --- | --- |
| Archived prior public runner routing | 1 | 2 intended failures, 2 controls passed |
| Fixed public runner routing | 0 | 4 passed |
| Earlier topic/preflight/warning and slot collision regressions | 0 | 23 passed |

RED failures are actual wrong KB evidence for valid angle KB-01 and failed research
for valid angle KB-custom. Controls exercise regular ANGLE-service and the KB pool.
GREEN checks exported topic ID/name, actual writer and reviewer research packs,
approved-stat evidence text/IDs, normal KB evidence/name, and assigned-source no-loop
behavior. Fixtures are offline and fictional; this does not prove semantic model quality.

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day2_angle_routing.py red --evidence-dir <fresh-external-directory>
& .\.venv\Scripts\python.exe scripts/prove_day2_angle_routing.py green --evidence-dir <fresh-external-directory>
& .\.venv\Scripts\python.exe -m pytest -q tests/test_day2_repairs.py tests/test_day2_slot_collision.py --basetemp <fresh-external-preservation-directory> --tb=short
```

Proof drivers exit0. RED archives the exact prior head, copies only the new test,
uses Python -S with explicit archive/installed-package paths and asserts product import
containment. No editable import leakage or async collection failure was accepted.
External exact commands/exits/output: `C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b\day2-angle-routing-proof\red.json`
and `green.json`; this report is copied there as `report.md`.

Related production prefix use was inspected: focused research names already-selected
typed KB evidence after source validation, and does not route assigned angle IDs.
It is unchanged. Original130 tests, schemas, pins, configuration contracts and prior
proof history are unchanged. Existing twitter-text/pkg_resources warning remains.
No live calls or full-suite run; final full suite and independent reviews belong to
the coordinator. Test resource released after focused verification.
