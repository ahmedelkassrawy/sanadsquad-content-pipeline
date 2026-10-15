# Strict sample packaging repair

Measured product: `6ba51353b7cd48cf729b180cbf0d4a1c4a67013b`.
Archived prior product: `591699920601330282efdbdfe5ced65042bf55e1`.
Numeric repair and coordinator-owned plans/runtime remain unchanged.

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day3_package.py red --evidence-dir <new-external-directory>
& .\.venv\Scripts\python.exe scripts/prove_day3_package.py green --evidence-dir <same-external-directory>
```

Both drivers exit0. Archived RED pytest exit1:26 unsafe acceptance failures,2 positive
controls passed. Product GREEN pytest exit0:28 passed,no skips. A separate focused
`python -m pytest -q tests/test_day3_package.py --basetemp <external> --tb=short`
also exited0 with28 passed. Base-to-head diff whitespace check exit0.
Exact command arrays/cwd/heads/exits/stdout/stderr:
`C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b\day3-package-final-proof\red.json`
and `green.json`; this report is copied there as `report.md`.
RED archives exact591, copies only the new test, uses Python-S with explicit installed
packages and asserts imported package containment. Intended failures were inspected;
no import/async infrastructure failure is accepted as proof.

Tests generate actual six-slot offline public runs, once with valid Pillow PNGs and once
with renderer failure producing valid spec_json fallbacks. Both package controls preserve
CSV bytes, exact manifested snapshot files, referenced images/specs and56 schemas.
Refusals cover synthetic snapshot.env/image/root private extras, forged manifest extras,
snapshot hash/run identity mismatch, corrupt referenced PNG/spec, escaping reference,
source/destination overlap, source alias/snapshot directory/destination-parent junctions.
Windows junction cases actually ran; no real credentials were read or copied.

The packager validates everything before creating the target, holds run ownership,
requires manifest hashes AND configuration-derived expected snapshot structure, checks
export/final/visual consistency and PNG/spec validity, then writes only validated bytes.
It rejects unexpected directories/files instead of recursively copying directories.
Known private runtime files are explicitly excluded. README documents the strict boundary.
This does not replace coordinator content/provenance/secret inspection of the real sample.

No full suite,live call,.env/key read,vault/PR/nested agent action. Runtime,schemas,pins,
numeric guard and plan/manifest unchanged. Existing parser/pkg_resources warning remains.
Independent final security/general/contract review,assembled release suite and actual
six-slot live sample remain coordinator-owned/pending. Test resource released.
