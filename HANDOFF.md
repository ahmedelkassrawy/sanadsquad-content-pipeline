# Handoff: final sample pending (2026-10-15)

Written for the next model or session picking up the SanadSquad content pipeline. The earlier Day 1 handoff is [_archived_HANDOFF-day1.md](_archived_HANDOFF-day1.md).

## Read first

1. [DESIGN.md](DESIGN.md) is the authority. Section 6 wins over section 2.
2. [plans/5-day3-delivery.md](plans/5-day3-delivery.md), the sections from "S4" onward, and [plans/5-day3-delivery.manifest.json](plans/5-day3-delivery.manifest.json) (slices S4 to S6).
3. This file.

## Where things stand

- Worktree `D:\me\SanadSquad-day3`, branch `feat/day3-delivery`, pushed to origin. Issue #5, draft PR #6 (not merged; merging is Kassra's call). Canonical repo root `D:\me\SanadSquad` is on `main`; do not work there.
- Full suite: 332 passed at `dbaa520` (`PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider`).
- The pipeline works end to end. What is missing is one clean six-slot sample at the current head, packaged into `output/sample-week`, plus the docs and PR updates that go with it.

## What changed this session (754115a..dbaa520)

| Commit | Change | Author | Review |
|---|---|---|---|
| c1df937 | Writers get measured length and a word budget on retry; refused advisor edits record `failed_output`, not `failed_api` | Opus coordinator | Opus CLEAN |
| fd608e7 | Advisor prompt: edits only target instruction files, with short verbatim excerpts | Sonnet 5.5 | Opus CLEAN |
| f790919 | KB-04..20 enriched from SWC, Solidity docs, EIP-6780 and OWASP SC Top 10; still `pending_kassra_review` | Sonnet 5.5 | Opus CLEAN (6 entries checked sentence by sentence) |
| 26a2a36 | Writer-only length notes kept out of summary reasons; `max_words` at least 1 | Codex gpt-6.1-sol | Opus CLEAN |
| 1eeea26 | Brand voice bans U+2011 non-breaking hyphen (Kassra approved) | coordinator | Opus CLEAN |
| 09fbfdd | `live_proof --allow-unpriced` so free models can run the proof | Codex | Opus CLEAN |
| 955ce39 | `MAX_CONCURRENCY` / `--max-concurrency` run-wide limiter; HTTP 429 waits 5, 10, 20 s or Retry-After (cap 60) | Codex | Opus BLOCKED on Gemini path, fixed below |
| dbaa520 | Gemini rate limits get the 429 backoff | Codex + coordinator test update | Not re-reviewed (one-line fix, red then green) |

Plan commits: 37514b7, ad1a6d0, e5c133c, f852e64.

## Live evidence (local only, under `.runs/`, excluded from git via `.git/info/exclude`)

- Paid gpt-4.1-mini weeks (before Kassra's free-only rule): `gpt41mini-week`, `-week2`, `-week3`. Week 3 shipped 6/6 for USD 0.047, but the content audit found unsupported padding from thin KB entries, which led to the KB enrichment.
- Free model `openrouter:nvidia/nemotron-3-super-120b-a12b:free`:
  - `free-super-week1` (head f790919, enriched KB): 6/6 shipped, first attempt each, zero uncertain tokens. Audit: grounded, minor stretches only; U+2011 hyphens (now banned).
  - `final-09fbfdd`: grounding control PASS (accepted supported, rejected the planted 97.3 percent claim as `unsupported_claim`); sample 2/6 because of 429s plus two correct rejections.
  - `final-955ce39`: every call 429. The key is on OpenRouter's free tier (`is_free_tier: true`), which allows about 50 free-model requests a day; the quota was exhausted.
- Other free models: nemotron-3-ultra fails intermittently, inkling returns 403 for this key, gemma-4-31b hits 429 quickly.
- KB claim ledger and fetched sources: `.runs/s5-evidence/` (168 quotes, all string-matched).

## Rules to keep

- Free OpenRouter models only for live runs. Ask Kassra before any paid model.
- Kassra's model routing: implementation by Codex `gpt-6.1-sol` (medium) or Sonnet 5.5; independent review by Opus 5.5; keep Claude usage low.
- Codex on this machine only works with: `codex exec --json --color never --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -m gpt-6.1-sol -c 'model_reasoning_effort="medium"' -c 'windows.sandbox="elevated"' -s workspace-write -c 'approval_policy="never"' -C D:/me/SanadSquad-day3 --output-last-message <file> - < brief.md` (Kassra approved these flags on 2026-10-15). Plain `codex exec` fails sandbox setup.
- Ship discipline (`~/.claude/skills/ship`): readiness check with `scripts/check-dispatch-packet.mjs` before dispatch; the checker hashes working-tree bytes (CRLF), not committed blobs.
- Never print or commit `.env`. Never weaken the reviewer, thresholds or retry counts to make a run ship. Never mix artifacts across runs.

## Next steps

1. **Final sample** (after the OpenRouter free quota resets at midnight UTC, or after Kassra adds credit):
   `.venv/Scripts/python.exe -B -m content_pipeline.live_proof --live --mode both --allow-unpriced --max-concurrency 2 --model openrouter:nvidia/nemotron-3-super-120b-a12b:free --output .runs/final-<head>`
   It needs roughly 40 to 60 requests. If it ships fewer than six, read `run_summary.json` and `usage.jsonl` before rerunning; correct reviewer rejections are not bugs.
2. Audit every post against `research_log.json` evidence by hand.
3. Package: `.venv/Scripts/python.exe scripts/package_sample.py --source .runs/final-<head>/sample-week --output output/sample-week`, and keep `sample-provenance.json` with it. Note: `output/` is in `.gitignore`; decide with Kassra whether to force-add the packaged sample (DESIGN 2.12 says the sample is committed).
4. Docs: README "Cost and verification" still says the sample is pending; document `--max-concurrency`, `--allow-unpriced` on live_proof, the free-tier quota and the advisor limitation below. Update ARCHITECTURE.md limitations. Run `/doc-truth` style verification against code.
5. Update PR #6 description (use the `pr` skill), then ask Kassra about merging.
6. Save memory with `/brain-save` (the vault has no project folder for `SanadSquad-day3` yet).

## Known open items

- Advisor fails on nemotron-super (`failed_output`): the model cannot produce the larger AdvisorOutput. It is optional and recorded; a different free model for `agents/advisor/agent.yaml` could be tried, but `--model-override` overrides every agent.
- Low review findings not fixed: KB-08 lacks the "SWC no longer maintained" note; no test pins the U+2011 ban.
- `dbaa520` was not re-reviewed by Opus.
- Retry-After handling has not been seen against a real 429 response.
- KB entries remain `pending_kassra_review`.
