# Verification

The repository separates offline behavior checks from live provider evidence. The offline
suite uses scripted transports and real public interfaces; it makes no model, search or image
API calls. It verifies orchestration, parallel slot isolation, retries, hard validation,
grounding records, review scoring, budgets, resume safety, image fallbacks and export files.

## Run the offline gate

From the repository root, using the project virtual environment:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m ruff format --check .
& .\.venv\Scripts\python.exe -m pip check
$schemaOutput = Join-Path ([System.IO.Path]::GetTempPath()) ("sanadsquad-schemas-" + [guid]::NewGuid())
& .\.venv\Scripts\python.exe -m content_pipeline.schemas --output $schemaOutput
git diff --no-index --exit-code -- schemas $schemaOutput
& .\.venv\Scripts\python.exe run_week.py --help
& .\.venv\Scripts\python.exe run_piece.py --help
```

The schema command uses a fresh temporary folder and the following command compares every
generated file with the committed `schemas/` directory. A difference exits nonzero.

## What the tests prove

- Each plan slot runs in its own failure boundary, so one failed slot does not stop the week.
- Empty topics are assigned without collisions when the configured topic pool permits it.
- Writers receive a grounded research pack and cannot call the research tools directly.
- Code enforces tweet count, weighted length, banned content, link allow-lists and source IDs.
- Rejected content gets one feedback retry and is dropped after a second rejection.
- Only accepted content reaches the visual stage and the CSV exporter.
- Model usage, uncertain charges, external calls and terminal outcomes are recorded.
- Resume refuses altered inputs or stage records and prevents simultaneous ownership.
- The Pillow image path and the Cloudflare fallback path produce inspectable artifacts.

These are behavior and contract checks. Scripted model responses do not prove that every live
model will make sound editorial or grounding judgments.

## Live sample evidence

`output/sample-week/` is a complete six-piece run made with real Anthropic calls (Opus 5.5
reviewer, Sonnet 5.5 for every other agent), with the brief's `--set thread_2="oracle manipulation"`;
the knowledge base covers it, so no web search ran. Cloudflare returned HTTP 429,
so the six images are Pillow cards.
Start with `run.log`, then `posts.csv`, `research_log.json` and `review_report.json`.
`sample-provenance.json` records the command, models, commit, token count and measured model
cost. It shipped 6 of 6; two threads shipped after a code rejection (an outline label) and their retry. `output/claude-run-with-drop/` is an
earlier run on the same models that shows rejections, retries and a drop:
`thread_2`'s first draft failed a code citation check, the second was rejected by the reviewer
for the source's wrong "101" total, and the slot was dropped. It excludes the
SQLite checkpoint, lock and identity files and therefore cannot be resumed.

`output/failed-source-example/` is a live single-piece run on a made-up topic: research finds
nothing that supports it and the slot ends `failed_research`, with the error in `run.log`.

`output/free-run-with-drops/` holds the readable files of a free run (Ministral writers,
Nemotron reviewer) on the older code that shows reviewer rejections and drops: 3 of 6 shipped.
`output/free-run-6-of-6/` is the same free setup on the round-4 code: 6 of 6 shipped, with two
code rejections and one reviewer rejection each fixed on the retry.

## Boundaries not claimed

- The Cloudflare image backend is unit-tested with mocked HTTP responses. Every live call so far
  returned HTTP 429, so no generated background has been checked live; the sample's cards are
  Pillow-drawn.
- Provider availability, quotas and free-tier pricing can change independently of this code.
- The knowledge base remains pending owner
  review before real publication.
- Resume is not exactly-once billing if a process dies after a provider charges a call but before
  its result is persisted.
- No scheduler-specific import or publishing integration is included; the deliverable stops at
  the required CSV.

See the README's known-limitations table for the corresponding next steps.
