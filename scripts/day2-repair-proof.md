# Day 2 review repair evidence

Product measured: `b9edbb55e2c0bbf6b29d9082cce79eeefc0226b5`.
Prior archived product: `f4c1fd07cd2cd4e3faadb5e44d80597b83300c3d`.

| Finding | Prior exit / result | Product exit / result |
| --- | --- | --- |
| G-P2: reserve later valid KB/angle proposals, duplicate precedence, exclusions and exhausted pools | 1 / 5 failed | 0 / 5 passed |
| C-P2: malformed angles preflight through Python and CLI | 1 / 13 failed, 1 existing missing-file positive control passed | 0 / 14 passed |
| C-P3: normal CLI budget estimate before factories | 1 / 1 failed | 0 / 1 passed |
| Seven relevant original caller/history/pool/estimate preservation tests | not repeated on prior head | 0 / 7 passed |
| JSON schema exporter | not repeated on prior head | 0 |

Reproduce from the project venv, using a fresh external directory:

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day2_repairs.py red --evidence-dir <external-directory>
& .\.venv\Scripts\python.exe scripts/prove_day2_repairs.py green --evidence-dir <external-directory>
```

Both proof drivers exited 0. RED uses `git archive`, copies only the regression file,
starts Python with `-S`, explicitly adds archived source and installed packages, and
asserts the imported package belongs to the archive. This prevents editable-import leakage.
GREEN runs real public callers with injected offline transports and external test outputs.

Exact commands, captured output and numeric exits:
`C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b\day2-repair-proof\red.json`
and `day2-repair-proof\committed\green.json` in the same scratch directory.
The first green run preceded the product commit; committed/green.json is authoritative.

No live requests or model spending. Existing 107 test cases and original proof history
remain unchanged. The coordinator owns the final full suite and independent re-review.
The existing twitter-text/pkg_resources deprecation warning remains. No semantic
grounding quality claim follows from injected offline responses.
