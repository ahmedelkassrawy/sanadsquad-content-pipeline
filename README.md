# SanadSquad content pipeline

This repo produces one week of X (Twitter) content for a smart contract audit firm.
It reads a weekly plan of 6 slots (3 technical threads, 3 marketing posts). It researches
each topic, writes each piece, checks it with code and with a reviewer, makes an image
for each accepted piece, and exports `posts.csv` (columns `Post Text, Image`).

- How it is built and why: [ARCHITECTURE.md](ARCHITECTURE.md)
- Settled design decisions (section 6 wins): [DESIGN.md](DESIGN.md)
- Test and proof notes: [docs/verification.md](docs/verification.md)
- A finished run you can open now: [output/sample-week](output/sample-week)

The firm in the inputs, **Sentinel Grove**, is fictional. Its statistics in
[inputs/brand-voice.md](inputs/brand-voice.md) are demo fixtures. Replace them and set
`brand_name` before using real claims. The 20 knowledge base entries in
[inputs/kb.json](inputs/kb.json) are still marked `pending_kassra_review`.

## Setup

You need Python 3.11 or newer. The pinned packages were exercised on Windows with
Python 3.13. macOS/Linux commands are given below but were not run for this README.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS or Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Then put your API keys in `.env`. The file is ignored by git. Real environment variables
override `.env`. A harmless `pkg_resources` UserWarning appears on start-up; ignore it.

## API keys

Put keys in `.env` or in real environment variables (these win over `.env`). Never put
them in a committed file. A model is written `provider:model`, and the provider name
decides which key is read.

**What the committed config needs.** The thread writer, the marketing writer and the
reviewer are set to `anthropic:claude-sonnet-5-5` in their `agent.yaml` files. The other
agents (research, visual, advisor) use `DEFAULT_MODEL`, which is
`openrouter:openai/gpt-4.1-mini` unless you change it. So a plain run needs
`ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY`. Both models are priced, so no
`--allow-unpriced` is needed.

**Claude is a paid model.** In the committed sample, a full week of Claude calls for the
writers and reviewer cost about 0.24 USD. The default budget stops a run at 1 USD.

**To reproduce the committed sample** use `ANTHROPIC_API_KEY` and `MISTRALAI_API_KEY` and
the command under "The committed sample". It sets Mistral as the default model for
research, visual and advisor.

**A free-model alternative.** Put the reviewer and marketing writer on NVIDIA's free tier,
as the earlier free sample did (commit `c234f25`, in git history only). Edit the `model:`
line of `agents/reviewer/agent.yaml`, `agents/marketing_writer/agent.yaml` and
`agents/thread_writer/agent.yaml`. Use
`nvidia:nvidia/nemotron-3-super-120b-a12b` for the reviewer and the marketing writer; set
the thread writer to `null` to use `DEFAULT_MODEL`. Then you need `NVIDIA_API_KEY` and
`--allow-unpriced` (free models have no price, so without the flag those calls are refused
and the slots end `failed_budget`). To keep the whole run free, also use the sample's
`--default-model mistralai:ministral-14b-latest --reasoning-effort-override none` with
`MISTRALAI_API_KEY`; otherwise the other agents use the paid OpenRouter default.

**A one-key run.** Add `--model-override openrouter:openai/gpt-4.1-mini`. It replaces the
model of every agent, including the three Claude ones. You then need only
`OPENROUTER_API_KEY`.

| Key | Needed when the model starts with |
| --- | --- |
| `OPENROUTER_API_KEY` | `openrouter:` (the default model) |
| `NVIDIA_API_KEY` | `nvidia:` (only in the free-model alternative) |
| `MISTRALAI_API_KEY` | `mistralai:` (the committed sample's default model) |
| `GROQ_API_KEY` | `groq:` |
| `GEMINI_API_KEY` | `google_genai:` |
| `OPENAI_API_KEY` | `openai:` |
| `ANTHROPIC_API_KEY` | `anthropic:` (writers and reviewer in the committed config) |
| `OPENCODE_API_KEY` | `opencode:` |
| `<PROVIDER>_API_KEY` | any other LangChain provider (its package must be installed) |
| `TAVILY_API_KEY` | optional: web search tries Tavily first. Without it, DDGS is used and needs no key. |

You only need keys for providers your run uses. If a key is missing, the first call to
that provider fails, that slot is recorded as failed, and the run goes on.

Other settings are optional and listed in [.env.example](.env.example). A command-line
flag beats the environment, which beats `.env`.

Free tiers change. What we saw on 2026-10-15:

- OpenRouter free models: 50 requests per day.
- Gemini 2.5 Flash free tier: 20 requests per day.
- Groq free tier: 8,000 tokens per minute.
- Mistral free plan: `ministral-14b-latest` at 937,500 tokens per minute and 30 requests
  per minute.
- NVIDIA free key: worked for `nemotron-3-super-120b-a12b`.

A full week needs more calls than the OpenRouter and Gemini free daily limits allow, so
use a paid key or the sample setup below.

## Run

The brief's example commands, exactly as written there:

```
python run_week.py --plan plan.json --output output/week-42
python run_week.py --plan plan.json --set thread_2="oracle manipulation"
python run_piece.py --type tech-thread --topic "reentrancy"
```

In this repo the plan lives at `inputs/plan.json`, not `plan.json`. Use this path, or
copy the file to the repo root first. The committed config needs
`ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` (see API keys). Run from the repo
root, with the venv Python:

```powershell
& .\.venv\Scripts\python.exe run_week.py --plan inputs/plan.json --output output/week-42
& .\.venv\Scripts\python.exe run_week.py --plan inputs/plan.json --set thread_2="oracle manipulation"
& .\.venv\Scripts\python.exe run_piece.py --type tech-thread --topic "reentrancy"
```

With one `OPENROUTER_API_KEY` only, add
`--model-override openrouter:openai/gpt-4.1-mini`.

On macOS/Linux use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

Useful facts:

- Without `--output`, the run folder is `output/<UTC time>-<random>`. An existing folder is
  never overwritten.
- `--set slot_id=topic` sets a topic only. It is not evidence. The topic still has to be
  backed by a knowledge base entry or a fetched web page, or the slot fails.
- `run_piece.py` runs one slot (`piece_1`) through the same graph.
- The summary JSON goes to stdout. The start-up estimate line goes to stderr. On an error the
  command prints only `pipeline_error:<ErrorName>` and exits with code 2.
- Slot ids must be safe file names and unique ignoring case.

Flags for both commands: `--root`, `--output`, `--resume`, `--estimate`, `--no-advisor`,
`--default-model`, `--model-override`, `--reasoning-effort-override`, `--max-tweet-chars`,
`--max-concurrency`, `--token-budget`, `--cost-budget`, `--allow-unpriced`.
`run_week.py` adds `--plan` and `--set`; `run_piece.py` adds `--type` and `--topic`.
Run `run_week.py --help` for the list.

### The committed sample

The sample in `output/sample-week` came from a live run at commit `9e19ac2`. This is the
exact command (also saved in `output/sample-week/sample-provenance.json`):

```
python run_week.py --plan inputs/plan.json --output <run> --default-model mistralai:ministral-14b-latest --reasoning-effort-override none --allow-unpriced --max-concurrency 2
```

It ran with `MODEL_OVERRIDE` empty. Model routing for that run:

- The thread writer, marketing writer and reviewer used `anthropic:claude-sonnet-5-5`
  (set in their `agent.yaml`; needs `ANTHROPIC_API_KEY`; paid).
- Research, visual and advisor used `mistralai:ministral-14b-latest` (the default model
  from the command; needs `MISTRALAI_API_KEY`; free tier).
- `--reasoning-effort-override none` applies to every agent, Claude included. Mistral's free
  `ministral-14b` answers HTTP 400 when `reasoning_effort` is `low` or `medium`.
- `--allow-unpriced` is needed because the price database has no price for the Mistral model.

Result: 6 of 6 pieces shipped, all on the first attempt. 112,378 tokens, 0.2378 USD
confirmed. That USD is the Claude calls only; the Mistral calls are unpriced and count as
0. The advisor failed with `failed_output`; it is optional and the failure is recorded.
Every shipped post was checked by hand against `research_log.json` on 2026-10-15.

The earlier free sample (commit `c234f25`, in git history) used Mistral plus NVIDIA
`nemotron-3-super-120b-a12b` for the reviewer and marketing writer: 6 of 6 shipped, 100,977
tokens, 0 USD. See "A free-model alternative" above to switch back.

## Cost and budget

Defaults are 250,000 tokens and 1 USD for the whole run, including the advisor. Before each
model call the guard reserves the estimated input plus the full output cap. If that does not
fit, the call does not start and the slot is marked `failed_budget`. Finished slots still
export. It is a guard, not a hard cap: estimates can be wrong and providers can bill more.

`--estimate` prints a worst-case ceiling as JSON and exits. It starts no model or search
client, makes no network call and writes no run folder. The ceiling assumes every retry
happens, so it is far above a real run (about 11 million tokens for the committed config,
against 112,378 used by the sample). A model with no known price is refused unless you pass
`--allow-unpriced`; then only the token limit applies. USD covers model calls only. Search
and image calls are counted in `external-usage.json` but not priced.

## Look at a run folder

Start with `posts.csv`, then `run_summary.json`. Every record in the JSON files carries a
`slot_id` and an `attempt` number. JSON schemas are in [schemas/](schemas/); they are
generated by `python -m content_pipeline.schemas --output schemas`.

| File | What it holds |
| --- | --- |
| `posts.csv` | The export: `Post Text, Image`, one row per shipped piece, in plan order. A thread is one cell with one tweet per line. Image paths are relative to the run folder. |
| `posts.json` | The same pieces with each tweet as a separate list item. |
| `plan.resolved.json` | The plan after `--set` and topic choice, with warnings. |
| `research_log.json` | Every source found for every slot, with its evidence text and status. |
| `drafts.json` | Each draft as the writer wrote it, the version after safe fixes, the fixes applied and any rule violations. |
| `review_report.json` | Scores per criterion, reasons, and the accept or reject decision. |
| `visuals.json`, `images/` | The image spec and the PNG card (or spec JSON) for each shipped piece. |
| `final.json` | The approved text and image of each shipped piece. |
| `run_summary.json` | One outcome per slot (`shipped`, `dropped_quality`, `failed_research`, `failed_api`, `failed_budget`, `failed_output`), tokens, cost, advisor status. |
| `usage.jsonl`, `ledger.json`, `external-usage.json` | One line per model call, the budget ledger, and search and image counters. |
| `advisor.json`, `advice.md`, `advice.patch` | Suggested instruction changes. Never applied for you. |
| `run.log` | Completion log. |
| `snapshot/` | A copy of the inputs and instruction files used by this run. |
| `checkpoint.sqlite`, `state.json`, `run_identity.json`, `runtime-settings.json` | Resume state. A live run has them. The committed sample leaves them out, so it cannot resume. |

A run with zero shipped pieces still writes a header-only `posts.csv` and a summary.

## Resume

```powershell
& .\.venv\Scripts\python.exe run_week.py --resume output/week-42
& .\.venv\Scripts\python.exe run_week.py --resume output/week-42 --token-budget 400000 --cost-budget 1.50
```

Resume continues from the saved checkpoint and the saved copy of the inputs. Edits to
the live files are ignored. The only settings you can change are `--token-budget` and
`--cost-budget`, and only upward. A finished run returns its saved summary. Two processes cannot use one
run folder at once. Billing is not exactly once: if a process dies after a call was billed
but before its result was saved, resume can pay for that call again.

## Advisor

After export, an advisor agent reads the run's evidence and suggests edits to the brand
voice, the writer instructions and the rubric. Code builds `advice.patch` from its edits,
refuses edits to machine rules or to lines with numbers, and never applies anything.
Read the patch yourself before using it. Turn the advisor off with `--no-advisor` or
`ADVISOR_ENABLED=false`. If it fails, the failure is recorded and the posts still ship.

## Checks you can run offline

These make no model or search calls. Python is the venv Python.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m content_pipeline.schemas --output <new-folder>
& .\.venv\Scripts\python.exe -m content_pipeline.live_proof
```

`live_proof` without `--live` only prints `NOT_RUN`. With `--live` it calls real models
(reviewer control and a full sample); see its `--help`. Tests use scripted models, so they
prove routing and rules, not the quality of a real model's judgement.

## Known limitations and what we would do next

| Limitation | Next step |
| --- | --- |
| `--reasoning-effort-override` applies to every agent. The sample therefore ran the Claude reviewer at effort `none`, although its `agent.yaml` asks for `medium` (DESIGN 2.7). | Add a per-agent effort override, so only agents on a model that rejects effort are changed. |
| The advisor failed with `failed_output` on the Mistral model in the sample. It is optional and the failure is recorded. | Point `agents/advisor/agent.yaml` at a stronger model and rerun. |
| In the committed sample, 5 of 6 image cards use the spec built by code (`visual_fallback`) because the visual agent's spec failed or failed the overlay check. Cards are plain Pillow cards; the Cloudflare image backend is not built. | Find out why the Mistral visual agent output fails the overlay check. Add a real image backend behind the existing spec. |
| The education marketing angle was dropped for length (378 to 395 characters) on earlier configurations. The prompt rewrite since then shortened its recipe, and the committed sample shipped it at 215 characters. It can still fail on weaker writer models; the piece is then dropped after its one retry, which is the intended behaviour. | Keep watching this angle when you change the writer model. |
| Connection and timeout errors from Groq and Gemini are not retried. A Groq HTTP 400 `tool_use_failed` gets no repair attempt. | Add those providers' exception classes to the retry list. |
| A misspelled provider name that still looks valid fails at its first model call, not at start-up. | Check at start-up that LangChain knows the provider. |
| Free models are unpriced, so their calls count as 0 USD (in the sample, only the Claude calls are costed). The code already uses the cost a provider reports (OpenRouter does), but providers that report none have no price. | Add a way to supply prices for such models. |
| `tests/test_pipeline.py` (line 281) reads `final.json` entry 0 and assumes `thread_1` finishes first. It can fail if rendering is delayed. | Look the piece up by `slot_id`. |
| Post text is never edited after a run. Prompt changes made after the sample (writers are told to answer their opening question and not to make evidence more specific; the marketing rubric scores an unanswered opening question as weaker and treats more-specific-than-evidence wording as unsupported) apply to future runs only, so the committed posts do not reflect them. | Make a fresh sample from the final prompts before any real use. |
| Knowledge base entries are not reviewed by the owner, and brand statistics are fictional demo fixtures. | Owner review of `kb.json`; replace the demo statistics. |
| Only run on Windows with Python 3.13. Card fonts are Windows fonts first, then DejaVu, then Pillow's default, so cards can look different elsewhere. | Run the offline checks and a sample on macOS and Linux. |
| Resume is not exactly-once for billing (see Resume). | Accept it, or move to a durable executor if this becomes a daily service. |

The exported CSV follows the brief's format. No particular scheduling tool's import
format is claimed.
