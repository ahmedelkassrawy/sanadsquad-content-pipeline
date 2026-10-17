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
& .\.venv\Scripts\python.exe -m content_pipeline.schemas --output <new-empty-folder>
& .\.venv\Scripts\python.exe run_week.py --help
& .\.venv\Scripts\python.exe run_piece.py --help
```

The generated schemas should match the committed files in `schemas/`. Use a fresh output
folder so verification does not overwrite tracked files.

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

`output/sample-week/` is a complete six-piece run made with real Mistral and Anthropic calls.
Start with `posts.csv`, `run_summary.json`, `research_log.json` and `review_report.json`.
`sample-provenance.json` records the command, models, commit, token count and measured model
cost. The sample shipped 6 of 6 pieces and its posts were manually checked against the saved
research evidence. The advisor failed safely and did not affect the exported posts.

The sample predates later image-spec repair improvements, so it demonstrates the recorded run,
not the best expected fallback rate of the current code. It also excludes the SQLite checkpoint,
lock and identity files and therefore cannot be resumed.

## Boundaries not claimed

- The Cloudflare image backend is covered with mocked HTTP responses; no real Cloudflare image
  call is claimed.
- Provider availability, quotas and free-tier pricing can change independently of this code.
- The sample firm and its statistics are fictional, and the knowledge base remains pending owner
  review before real publication.
- Resume is not exactly-once billing if a process dies after a provider charges a call but before
  its result is persisted.
- No scheduler-specific import or publishing integration is included; the deliverable stops at
  the required CSV.

See the README's known-limitations table for the corresponding next steps.
