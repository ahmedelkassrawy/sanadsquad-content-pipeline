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
400,000 tokens and 1 USD per run.

The ceiling is far above a real run on purpose: it assumes every call uses its full output cap
and every retry, repair and search turn happens (about $52 on the committed Claude models; real
runs cost about $0.41 to $0.50). It is a warning, not a plan. What keeps a run inside the $1
budget is the guard in `model_client.py`, which reserves each call's worst case before sending
it and refuses the call (`failed_budget`) when it no longer fits.

The advisor, which suggests instruction edits after export, is optional and outside the
brief and off by default. Set `ADVISOR_ENABLED=true` in `.env` to turn it on.

## Reading a run

Each run folder (`output/week-42`, or `output/<UTC time>-<random>` without `--output`) holds
`posts.csv`, the deliverable. A generated `README.md` in the folder lists the result and
trace files it contains (internal resume files are not listed), and `run.log` shows what happened to each slot: research, each draft, reviewer
scores and reasons, and the outcome. `run_summary.json` has tokens and cost, in total and per agent. Finished examples are
below.

## Sample runs

- **Headline sample: [output/claude-run-with-drop](output/claude-run-with-drop)**, a live run on
  the committed Claude models, shipped 5 of 6 for $0.503 (123,644 tokens, from its
  `run_summary.json`). It shows the whole review loop: the reviewer rejected threads for
  copying their sources nearly word for word, and `thread_1` and `thread_2` shipped after a retry
  with that feedback. `thread_3` was rejected for an unsupported claim, and its retry failed a
  code check (`repeated_credit:swc`), so it was dropped. The three marketing posts shipped
  first try. Start with its `run.log`.
- **[output/sample-week](output/sample-week)**: a clean 6 of 6 run ($0.41) made on an earlier
  commit (`5f115ae`, per its `sample-provenance.json`).
- Most runs ship 4 to 6 of 6, and an occasional run ships fewer (see Known limitations). The gates are strict and drop weak pieces on purpose, so a
  drop is expected behaviour, not a bug.

Importing `posts.csv` into a scheduler: each row is one post. A thread is one cell, with its
tweets separated by newlines and already numbered (`1/8`, `2/8`). In a scheduler's thread
composer (Typefully, Buffer), paste each line as its own tweet. The `Image` column is a path
relative to the run folder (`images/slot-thread_1.png`); upload that file with the first
tweet. The CSV is UTF-8 with standard quoting. Import into a real scheduler has not been tested.

## Quality gates in short

Code checks tweet count and length, banned words and characters, links, citations, and that
multi-digit numbers appear in the evidence. An en dash is auto-fixed to a hyphen. Text is
first normalized (Unicode NFKC), so look-alike characters such as a full-width `＃` cannot
sneak a banned character or hashtag past the rules. The reviewer scores each criterion. Any score
below the minimum reviewer score a post type needs to ship (`min_score`, 3) rejects the draft,
even if the average passes.

## More documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): how it is built and why
- [docs/operations.md](docs/operations.md): flags, run folder files, resume, advisor, config, scripts, offline checks
- [docs/providers-and-cost.md](docs/providers-and-cost.md): keys, providers, free tiers, budget, price overrides, the committed sample
- [docs/verification.md](docs/verification.md): verification scope and commands

## Known limitations and what we would do next

| Limitation | Next step |
| --- | --- |
| The clean `output/sample-week` run shows two code rejections and retries (outline labels) but no reviewer rejection or drop. | See `output/claude-run-with-drop` for reviewer rejections, retries and a drop on Claude, and `output/failed-source-example` for a slot that fails closed. See `output/free-run-6-of-6` (free models, round-4 code: 6 of 6 after three retries) and `output/free-run-with-drops` (free models, older code: drops). |
| The general number check skips bare single digits ("3 steps") and numbers written as words ("three steps"); the firm-stat check enforces an approved number when it counts people or firm work ("12 engineers" fails) or counts nothing in a sentence about the firm's work ("We have completed 12." fails), and ignores technical counts ("3 functions"), dates, times and dotted versions; it reads spelled-out approved counts up to twenty. Cross-sentence joins ("...12 smart contract audits. All 3 auditors worked on each one.") remain semantic and stay with the reviewer. | Extend the check once false positives are measured. |
| Within-thread repetition uses stemmed content-word overlap plus repeated phrases; it excludes the closing tweet and asymmetric example/detail transitions, so semantic repetition with little shared language remains with the reviewer. | Add embeddings only after measuring their false-positive rate on accepted threads. |
| The reviewer (Opus) is a different model from the writers, which reduces shared blind spots but is still an LLM judgement. Tests use scripted models, so they prove routing and rules, not real judgement quality. | Run `live_proof --live` and hand-check a sample after every model change. |
| The advisor is off by default (`ADVISOR_ENABLED=false`) and did not run in the sample. It is optional and outside the brief. | Set `ADVISOR_ENABLED=true` to try it. |
| Cloudflare FLUX backgrounds are unit-tested with mocked HTTP. In live runs Cloudflare returned HTTP 429 (rate limit) for every image, so the sample's images are Pillow cards: brand header, title, the post's own code line for threads, an accent bar and a severity badge. | Check new images by eye after a prompt change; retry Cloudflare on a paid plan. |
| Connection and timeout errors from Groq and Gemini are not retried, and a Groq HTTP 400 `tool_use_failed` gets no repair attempt. A misspelled provider name fails at its first call, not at start-up. | Add those exception classes to the retry list; validate the provider at start-up. |
| A model with no known price is refused (`failed_budget`) unless you pass `--allow-unpriced`, which leaves only the token limit. The committed models are priced via `config/prices.yaml`. Search and image calls are counted, not priced. | Add prices for any other model you use. |
| Marketing variety is enforced by inputs, not by comparing finished posts: each angle has its own approved stats and opening, and the education angle takes a KB topic no thread uses that week. Nothing compares two finished posts. | Add a cross-slot text similarity check if two posts still read alike. |
| Weaker writer models vary from run to run. On the free setup, while the checks and prompt changes went in one at a time, four runs shipped 4, 2, 6 and 6 of 6; the last two used the checks and prompts as of those runs; the claim-word list was narrowed after them. That is a small sample. | Run a week and read `run.log` whenever you change a writer model. |
| The reviewer is an LLM and not fully consistent: it rejected "12 smart contract audits completed by a team of 3 auditors" in one run and passed it in the next. That case is now a code check (`check_stat_claims`: a stat's number keeps its noun, and two stats may not share a sentence), but other judgement calls still vary run to run. | Turn a recurring reviewer disagreement into a code check when it can be stated as a rule. |
| Knowledge base entries are not reviewed by the owner. | Owner review of `kb.json`. |
| Exercised on Windows with Python 3.13 only; card fonts differ elsewhere. | Run the offline checks and a sample on macOS and Linux. |
| A crashed run that is resumed can pay twice for a model call that had already been billed (see [Resume](docs/operations.md#resume)). | Accept it, or move to a durable executor for a daily service. |
| The model's topic choices are not saved, so a resume after a crash inside the topic node can re-ask and get a different answer; the affected slot then fails instead of swapping topics. Topic history also counts every sibling folder of the run folder (the committed `output/` samples included), so a first run next to them can warn `history_exhausted`. See [Topic selection](ARCHITECTURE.md#topic-selection-decision-and-alternatives). | Save `TopicChoices` to the trail and reuse it on resume; add an explicit history folder. |

The exported CSV follows the brief's format. No particular scheduling tool's import
format is claimed.
