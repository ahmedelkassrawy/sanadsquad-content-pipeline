# Handoff: start of Day 1

Written 2026-10-14 for the next session (Kassra or an agent) picking up the SanadSquad content pipeline.

## Where things stand

- **Design is settled.** Read [DESIGN.md](DESIGN.md) first; every decision there was agreed with Kassra over a 60-question design session. Don't reopen them without asking him.
- **The four open API questions are answered.** See DESIGN.md §4. One changed the design: on OpenRouter BYOK, `usage.cost` is only the fee, so cost = `usage.cost + cost_details.upstream_inference_cost`.
- **No code exists yet.** The repo has `DESIGN.md`, `HANDOFF.md` and `.gitignore`.
- **Repo:** private, [ahmedelkassrawy/sanadsquad-content-pipeline](https://github.com/ahmedelkassrawy/sanadsquad-content-pipeline), branch `main`.
- **The brief:** `C:\Users\Dell\Downloads\Telegram Desktop\AI_Engineer_Task_Multi_Agent_Content_Pipeline.pdf`.

## Deadline and plan

Target **Fri 2026-10-16** (hard deadline Sun 2026-10-18). Today is Day 1:

1. Project skeleton: `pyproject.toml` / `requirements.txt`, `.env.example`, folder layout from DESIGN.md §2.3.
2. Inputs: `inputs/brand-voice.md` (front matter + prose, neutral made-up firm name, fake stats as `STAT-01..`), `inputs/kb.json` (18 to 20 entries from SWC Registry / OWASP SC Top 10, with `aliases` and `references`; **Kassra reviews these**), `inputs/plan.json` (6 slots).
3. Pydantic contracts (`ResolvedSlot`, `ResearchPack`, `Draft`, `ValidationResult`, `Review`, `ImageSpec`, `ImageResult`, `UsageRecord`) + JSON Schema export to `schemas/`.
4. Model wrapper: `init_chat_model`, `include_raw=True`, usage capture, transient retry x3, truncation retry at 1.5x, and the **usage ledger** (reserve worst case under `asyncio.Lock`, settle after).
5. Pricing: provider cost first, else `genai-prices` `calc_price`; `LookupError` -> refuse unless `--allow-unpriced`.
6. Validator + citation checker (X-style 280 counting, links = 23).
7. Skills: `search_kb` (BM25 + aliases), `web_search` (Tavily -> `ddgs`), `brand_rules`.
8. pytest with a fake LLM covering validator, citations, budget refusal.

Cut order if behind (Day 3 items): Cloudflare adapter -> `--resume` flag -> advisor.

## Environment facts

- Python: `C:\Users\Dell\miniconda3\python.exe` (3.13). `python3` is a Store alias; don't use it.
- Already installed in miniconda: `langgraph` 1.1.9, `langchain-core` 1.6.0, `langchain-anthropic` 1.4.0, `langchain-openai` 1.6.0, `langchain-google-genai` 4.2.1, `pydantic` 2.12. **Not installed:** `genai-prices` (tested in a scratch venv only), `rank-bm25`, `tavily-python`, `ddgs`, `pillow` (check), `python-dotenv`. Prefer a project venv over adding to miniconda.
- Keys: Kassra has OpenRouter, Gemini and OpenCode Zen keys. Only `GEMINI_API_KEY` is set in the shell; the rest go in `.env` (gitignored).
- Don't use LiteLLM: versions 1.82.7/1.82.8 were compromised on PyPI in March 2026, and it isn't needed.

## Things to remember while building

- Agents are plain classes; LangGraph nodes are thin glue. No agent reads `WeekState`.
- Rules in code, judgement in the model: the validator is code, not an agent.
- Instructions are files: no prompt strings in Python.
- Record every call in `usage.jsonl` with `cost_source`.
- Before claiming anything works, run it (tests or a live call). List anything unverified under "Not verified".
- Run `/ship` conventions per Kassra's global CLAUDE.md for implementation work.
