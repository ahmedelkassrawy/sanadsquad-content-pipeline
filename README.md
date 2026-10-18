# SanadSquad content pipeline

This repo produces one week of X (Twitter) content for **Sentinel Grove**, a smart contract
audit firm. It is a LangGraph multi-agent pipeline. It reads a weekly plan of 6 slots (3
technical threads, 3 marketing posts), researches each topic, writes each piece, checks it
with code (brand rules, length, citations, numbers) and with a reviewer from a different model
than the writers, makes an image for each accepted piece, and exports `posts.csv` (columns
`Post Text, Image`). The brand statistics in [inputs/brand-voice.md](inputs/brand-voice.md)
are the firm's facts; the 20 knowledge base entries in [inputs/kb.json](inputs/kb.json) are
still marked `pending_owner_review`.

## Setup

You need Python 3.11 or newer. The pinned packages were exercised on Windows with Python 3.13.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS or Linux (not run for this README):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

A harmless `pkg_resources` UserWarning appears on start-up; ignore it.

## API keys

Put keys in `.env` (git-ignored; copy from [.env.example](.env.example)) or in real
environment variables, which win over `.env`. A model is written `provider:model` and the
provider name decides which key is read. The committed config needs:

- `ANTHROPIC_API_KEY`: every agent. The reviewer runs on Claude Opus 5.5
  (`anthropic:claude-opus-5-5`), a larger model than the writers; the writers, research,
  visual and advisor run on Claude Sonnet 5.5 (`anthropic:claude-sonnet-5-5`). All use
  reasoning effort low.
- Optional: `TAVILY_API_KEY` (web search; DDGS is used without it), `CLOUDFLARE_ACCOUNT_ID`
  and `CLOUDFLARE_API_TOKEN` (FLUX image background; otherwise a Pillow card).

A free setup (Ministral writers, Nemotron reviewer) is described in
[docs/providers-and-cost.md](docs/providers-and-cost.md). With only an OpenRouter key, add `--model-override openrouter:openai/gpt-4.1-mini`. This
replaces every agent's model, so the reviewer also runs on the writers' model and you lose the
independent judge. Other providers, free-tier setups and the full key table are in
[docs/providers-and-cost.md](docs/providers-and-cost.md).

## Run

The brief's commands, exactly as written there. They work from the repo root; use the venv
Python (`.\.venv\Scripts\python.exe` on Windows, `.venv/bin/python` elsewhere) in place of
`python` if the venv is not activated:

```
python run_week.py --plan plan.json --output output/week-42
python run_week.py --plan plan.json --set thread_2="oracle manipulation"
python run_piece.py --type tech-thread --topic "reentrancy"
```

A relative `--plan` is resolved against the repo root (`--root`, default: this repo), not the
current folder, and falls back to `inputs/plan.json` when `plan.json` is missing there. `--set slot_id=topic` sets a topic only; it still needs knowledge base or web
evidence. `--estimate` prints a worst-case cost ceiling without any call. Default budget is
250,000 tokens and 1 USD per run.

The advisor, which suggests instruction edits after export, is optional and outside the
brief and off by default. Set `ADVISOR_ENABLED=true` in `.env` to turn it on.

## Reading a run

Each run folder (`output/week-42`, or `output/<UTC time>-<random>` without `--output`) holds
`posts.csv`, the deliverable. A generated `README.md` in the folder lists the result and
trace files it contains (internal resume files are not listed), and `run.log` shows what happened to each slot: research, each draft, reviewer
scores and reasons, and the outcome. `run_summary.json` has tokens and cost. A finished
example is in [output/sample-week](output/sample-week).

## Quality gates in short

Code checks tweet count and length, banned words and characters (an en dash is a banned character and is
auto-fixed to a hyphen; NFKC folding catches full-width look-alikes such as `＃`), links, citations, and that multi-digit numbers appear in the
evidence. The reviewer scores each criterion; any score below the type's `min_score` (3)
rejects the draft even if the average passes.

## More documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): how it is built and why
- [docs/operations.md](docs/operations.md): flags, run folder files, resume, advisor, config, scripts, offline checks
- [docs/providers-and-cost.md](docs/providers-and-cost.md): keys, providers, free tiers, budget, price overrides, the committed sample
- [docs/verification.md](docs/verification.md): verification scope and commands

## Known limitations and what we would do next

| Limitation | Next step |
| --- | --- |
| The committed sample shipped 6 of 6, so it shows a code-rule rejection and a retry but no drop. It was made before the claim-word check, length split, link note, markdown fix and STAT-04 were added. | Rerun it on the current commit. See `output/free-run-6-of-6` (free models, current code: 6 of 6 after three retries) and `output/free-run-with-drops` (free models, older code: drops). |
| The number check skips bare single digits ("3 steps") and does not see numbers written as words ("three steps"). Both are left to the reviewer. | Extend the check once false positives are measured. |
| The reviewer (Opus) is a different model from the writers, which reduces shared blind spots but is still an LLM judgement. Tests use scripted models, so they prove routing and rules, not real judgement quality. | Run `live_proof --live` and hand-check a sample after every model change. |
| The advisor is off by default (`ADVISOR_ENABLED=false`) and did not run in the sample. It is optional and outside the brief. | Set `ADVISOR_ENABLED=true` to try it. |
| The Cloudflare image backend is unit-tested with mocked HTTP; the sample's six FLUX backgrounds are its only live check. Without the keys every card is a plain Pillow card. | Check new images by eye after a prompt change. |
| Connection and timeout errors from Groq and Gemini are not retried, and a Groq HTTP 400 `tool_use_failed` gets no repair attempt. A misspelled provider name fails at its first call, not at start-up. | Add those exception classes to the retry list; validate the provider at start-up. |
| A model with no known price is refused (`failed_budget`) unless you pass `--allow-unpriced`, which leaves only the token limit. The committed models are priced via `config/prices.yaml`. Search and image calls are counted, not priced. | Add prices for any other model you use. |
| In the Claude sample, `marketing_1` and `marketing_2` open with near-identical questions. Each marketing angle now has its own opening in `writer.md`, but nothing in code checks hooks across slots. | Add a cross-slot similarity check if prompts alone prove not enough. |
| Weaker writer models vary from run to run. On the free setup, while the checks and prompt changes went in one at a time, four runs shipped 4, 2, 6 and 6 of 6; the last two used the current code. That is a small sample. | Run a week and read `run.log` whenever you change a writer model. |
| Knowledge base entries are not reviewed by the owner. | Owner review of `kb.json`. |
| Exercised on Windows with Python 3.13 only; card fonts differ elsewhere. | Run the offline checks and a sample on macOS and Linux. |
| Resume is not exactly-once for billing (see [Resume](docs/operations.md#resume)). | Accept it, or move to a durable executor for a daily service. |

The exported CSV follows the brief's format. No particular scheduling tool's import
format is claimed.
