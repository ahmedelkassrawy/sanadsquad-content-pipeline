# SanadSquad content pipeline

Produce a week of grounded X content through a fixed LangGraph workflow. Research
resolves evidence, configured writers draft each piece, code validates hard rules,
and a rubric reviewer decides whether claims are supported. Accepted pieces get a
Pillow card and CSV/JSON export. The advisor suggests instruction changes after export.

The repository ships **Sentinel Grove**, a fictional audit firm. Its approved statistics
are explicitly fictional demo fixtures. Set `brand_name` and replace the approved
statistics in [inputs/brand-voice.md](inputs/brand-voice.md) before using real company
claims. The 20 KB entries remain pending Kassra review. Severity badges and categories
are editorial educational labels, not official SWC/OWASP ratings.

## Setup

Use Python 3.11 or newer; the pinned environment was exercised on Windows/Python 3.13.
In PowerShell, with the chosen Python executable on PATH:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Put the selected provider's key in `.env` or the environment. `.env` is ignored by git
and excluded from snapshots. The default model is `openrouter:openai/gpt-4.1-mini`.

| Model reference | Required key |
| --- | --- |
| `openrouter:openai/gpt-4.1-mini` | `OPENROUTER_API_KEY` |
| `opencode:gpt-5` | `OPENCODE_API_KEY` |
| `openai:gpt-4.1-mini` | `OPENAI_API_KEY` |
| `google_genai:gemini-2.5-flash` | `GEMINI_API_KEY` |
| `anthropic:<supported-model-id>` | `ANTHROPIC_API_KEY` |

Provider availability and model access depend on your account. Optional `TAVILY_API_KEY`
selects Tavily; otherwise research can use DDGS plus public-page fetching. Live model
and web requests can cost money. No LiteLLM dependency is used.

## Run

```powershell
& .\.venv\Scripts\python.exe run_week.py --help
& .\.venv\Scripts\python.exe run_week.py --estimate
& .\.venv\Scripts\python.exe run_week.py --plan inputs/plan.json --set 'thread_2=oracle manipulation' --output output/week-42
& .\.venv\Scripts\python.exe run_piece.py --type tech-thread --topic reentrancy --output output/reentrancy
& .\.venv\Scripts\python.exe run_week.py --model-override google_genai:gemini-2.5-flash --output output/gemini-week
```

`--set` specifies a topic, not evidence. It still needs KB or fetched-page support.
`run_piece.py` uses the same graph. Both CLIs support `--root`, `--output`, `--estimate`,
`--default-model`, `--model-override`, `--max-tweet-chars`, `--token-budget`,
`--cost-budget`, `--allow-unpriced`, `--no-advisor` and `--resume`.
Use a new output directory: existing runs are never silently overwritten.
Slot IDs must be safe filenames and unique ignoring case on Windows.

CLI model/budget overrides take precedence over `.env`/environment and file defaults.
An agent's `agent.yaml` can override the default model; `--model-override` overrides
every agent. The editorial tweet limit may only lower X's weighted 280-character limit.
Normal startup prints a worst-case budget warning to stderr; stdout contains the final
summary. `--estimate` initializes no model/search clients and creates no run output.

## Inspect results

`posts.csv` has `Post Text, Image` columns and one row per accepted piece, in plan order.
Thread tweets occupy one cell with newlines. Image paths are relative to the run folder.
This is the brief's CSV format; no particular scheduler importer is claimed.
The exported text is exactly what passed validation and review, with code-added numbering.

Each run also keeps `posts.json`, `plan.resolved.json`, `research_log.json`, `drafts.json`,
`review_report.json`, `visuals.json`, `final.json`, `advisor.json`, `usage.jsonl`,
`ledger.json`, `external-usage.json`, `run_summary.json`, `advice.md`, `advice.patch`,
`images/`, `snapshot/`, `checkpoint.sqlite`, `state.json`, `run_identity.json`,
`runtime-settings.json` and `run.log`. JSON schemas are in [schemas/](schemas/).
Original and safely normalized drafts, sources, retries and terminal failures are retained.
Zero accepted pieces still produce header-only CSV and a summary.

Only accepted pieces get visuals. Code validates overlays against brand rules and saved
evidence, draws a severity badge when available, and renders a 1200x675 Pillow card.
If rendering fails, export points to the written ImageSpec JSON. Cloudflare image
generation is deferred; no Cloudflare adapter/live integration is claimed.

## Resume and advisor

```powershell
& .\.venv\Scripts\python.exe run_week.py --resume output/week-42
& .\.venv\Scripts\python.exe run_week.py --resume output/week-42 --token-budget 400000 --cost-budget 1.50
& .\.venv\Scripts\python.exe run_week.py --resume output/week-42 --estimate
```

Resume uses the saved snapshot and original thread identity, even after a directory rename.
Live policy edits are ignored; current provider credentials still come from the current
`--root` and environment. Only budget increases are allowed on resume. A completed run
returns its saved summary without initializing clients. An OS ownership lock rejects
simultaneous use of one run before ledger recovery or shared writes.

Only this Day 3 snapshot/checkpoint format is resumable. Missing, changed or incompatible
snapshots/checkpoints refuse startup. Day 2 finalized exports remain readable, but have no
resume migration. Keep the original local run folder; a packaged sample excludes its
private checkpoint/ownership files and cannot resume.

Checkpointed graph nodes are skipped. **Billing is not exactly once**: a process can die
after dispatch or after a billed response but before a node checkpoint. Unfinished nodes
may repeat, including completed sibling calls within an unfinished research `gather` node.
Saved trails are deduplicated; they do not independently checkpoint each tool/model action.
New call IDs retain lifetime confirmed/uncertain spend. Never-dispatched reservations are
released; unresolved dispatched charges stay uncertain. Saved external counters survive
resume; operations lost before their counter save can be absent from those counters.
Review, visual and advisor records also carry an exact request hash. A fully saved result
can be recovered if its node checkpoint was interrupted, keeping its trail and files
consistent. Missing hashes in an older unfinished run refuse compatible-stage recovery.

The advisor runs by default after export and before final accounting. Disable it for a
new run with `--no-advisor` or `ADVISOR_ENABLED=false`. It reads saved run evidence and
allowed snapshot instructions. Code derives a bounded unified diff from typed edits,
checks exact source hashes/paths, and rejects machine-rule changes. Each edit is checked
against its affected full lines: numeric-bearing lines cannot be added, removed or
changed, including edits of surrounding words. Unchanged numeric lines are allowed.
Numbers elsewhere in a file cannot authorize an edit.
The target set is each used type's `writer.md`, configured rubric, and brand voice.
Source files and accepted content are never changed. Review `advice.patch` yourself
before any manual application. This conservative text guard is not semantic fact checking:
review all suggested prose, including quantities written as words. Advisor
API/budget/output failure is recorded and does not prevent content export.

## Cost and verification

Defaults are 250000 tokens and USD 1 per run lifetime, including the advisor. The guard
reserves estimated input plus full output before dispatch. Estimates can overshoot;
provider billing may exceed a local prediction. Confirmed and uncertain totals are separate.
LLM USD is provider-reported when available, otherwise `genai-prices`; unknown models
refuse by default. `--allow-unpriced` opts into token-only guarding. Search/image calls
and Tavily credits are counted separately and are not included in LLM USD.

Gemini 2.5 uses bounded `thinking_budget`; Flash's `none` disables thinking, while Pro
retains its required minimum. Newer Gemini uses `thinking_level`. Thinking is included
in normalized output token billing. See [verification notes](docs/verification.md) for
installed-producer checks, offline/live evidence and their limits.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m content_pipeline.schemas --output <temporary-schema-directory>
& .\.venv\Scripts\python.exe scripts/prove_day3.py --evidence-dir <new-external-directory>
& .\.venv\Scripts\python.exe -m content_pipeline.live_proof
```

The last command makes no requests without `--live`. An operator can explicitly run:

```powershell
& .\.venv\Scripts\python.exe -m content_pipeline.live_proof --live --mode reviewer --model google_genai:gemini-2.5-flash --output <new-grounding-directory>
& .\.venv\Scripts\python.exe -m content_pipeline.live_proof --live --mode sample --model google_genai:gemini-2.5-flash --output <new-sample-directory> --cost-budget 1
& .\.venv\Scripts\python.exe scripts/package_sample.py --source <new-sample-directory>/sample-week --output output/sample-week
```

The reviewer control gives an unsupported invented claim a valid citation ID; it must
reject that claim and accept the supported control. The sample succeeds only if all six
pieces ship. Keep the sample provenance alongside its packaged artifacts. Tests inject
offline responses to prove routing and persistence; they do not prove semantic judgment.
Packaging verifies snapshot hashes against saved run identity and the exact files
referenced by the saved configuration, plus referenced PNG/spec files and matching
exports. Rubrics/research hints use `.md`, angles use `.yaml`. Unexpected files/directories
in the snapshot/images or run root refuse packaging before the destination is created;
known private runtime files are excluded. Source/destination overlap and symlinks,
junctions or reparse points are rejected. Only explicitly validated file bytes are copied.
The complete real six-slot sample is currently pending provider availability (quota reset
or available provider credits);
the retained failed attempts are not published as a successful sample. The
[architecture](ARCHITECTURE.md) explains extensions and the remaining limitations.
