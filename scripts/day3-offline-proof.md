# Day 3 offline implementation proof

Measured final product: `833a7ab20ea5f13d15f289b9b5601ddfd9ff23ef`.
Merged Day 2 base: `bc9cc75dbd78806ace0e6c4e953cc91e1e06e45b`.
All artifacts are external under
`C:\Users\Dell\.t3\scratch\2026-10-12-i-want-to-write-the-c75c9f5b`.

| Check | Exit | Actual result |
| --- | --- | --- |
| Archived merged-base resume | 1 | 8 intended absent-capability failures |
| Archived merged-base advisor | 1 | 7 intended absent-advisor failures |
| Archived merged-base Gemini producer | 1 | 6 reasoning/retry failures, 3 controls passed |
| Archived merged-base CLI --resume | 2 | Unrecognized --resume flag |
| Archived42e4778 SDK429 retry regression | 1 | 2 failures, 2 controls passed |
| Archived42e4778 after-trail crash recovery | 1 | 3 duplicate-operation failures |
| Archived84b988 inline source_ids export | 1 | Incorrectly shipped metadata |
| Product public resume/advisor/SDK/live-gate regressions | 0 | 43 passed |
| Product live harness without --live | 0 | NOT_RUN, no output/client initialization |
| Resume fresh-input bypass mutant | 1 | Checkpointed research/writer repeated |
| Advisor edit guard bypass mutant | 1 | Escaping edit incorrectly marked completed |
| Temporary schema export/equality | 0 | 56/56 structurally equal |
| Whole offline suite on84b988, before added metadata guard case | 0 | 176 passed; all134 originals preserved |
| Metadata/advisor/six-slot preservation after guard | 0 | 9 passed |
| pip check and base/head diff whitespace check | 0 | Clean |

Reproduce the base/head/mutation proof from the project venv:

```powershell
& .\.venv\Scripts\python.exe scripts/prove_day3.py --evidence-dir <new-external-directory>
```

Driver exit0. `day3-bookkeeping-final-proof/report.json` retains every exact command,
cwd, head, numeric exit and captured output. Earlier `day3-final-offline-proof/report.json`
and `full-suite.txt` retain the pre-metadata43-minus1 focused and176 full-suite evidence.
Archives run Python -S with explicit installed packages and asserted package containment;
no editable-current import leak or collection failure was accepted as feature proof.
Mutations execute external copies only. Original Day1/2 tests changed solely to add the
explicit AdvisorOutput offline fixture response; none of the134 cases was removed/weakened.

Real graph tests cover focused/topic checkpoints, writer/reviewer/visual subgraph
checkpoints, export/advisor boundaries, completed zero-call resume, snapshot/renamed
identity, forbidden settings/budget decreases, ownership (including simultaneous public
resume), canceled dispatched charge recovery, next call IDs, saved external counters,
and crash after saved stage trails. Saved stage results are tied to exact request hashes.
The actual unfinished research gather test confirms a saved sibling call can repeat;
trails deduplicate and both charges remain accounted. No exactly-once billing claim.
Advisor tests inspect actual bounded diff, git apply --check, byte-unchanged sources,
field/path/hash/frontmatter/stat rejection, billed malformed repair and failure isolation.

Fresh Day3 venv installed the existing pinned requirements with miniconda Python3.13;
pip check exited0. Installed LangGraph1.2.15/SQLite3.1.2, Google adapter4.4.2 and SDK2.30.1
were exercised (exact package pins remain in requirements.txt). SDK-shaped Gemini requests
and responses test reasoning config, normalized thoughts,429/503 retry and401 refusal;
provider exception text never enters usage/ledger. Official thinking/persistence links
and source-backed coverage are documented in docs/verification.md.

README/ARCHITECTURE were written after code behavior was exercised. Week CLI help,
week/piece estimate and ungated live module commands exited0. Current initial estimates
include advisor; they initialize no clients/search and create no output.

No worker live calls, model spend, nested agents, T3, vault writes or PR/merge actions.
Coordinator live history is separate: bounded real Flash and clarified Lite reviewer
controls passed; two real six-slot attempts failed and remain retained as failures.
One Lite output exposed inline source_ids, now guarded offline. A subsequent safe SDK
diagnostic confirmed the free-tier daily request quota exhausted (observed limit20,
retry delay34735 seconds); no pacing change or further blind Gemini week is planned.
No complete real sample is claimed. Coordinator owns provider availability/sample, independent
general/contract/security/docs review and final assembled full-suite/release gates.
Cloudflare deferred, KB owner approval pending, parser/pkg_resources warning documented.
