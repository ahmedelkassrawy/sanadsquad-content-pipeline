# Day 3 delivery - issue #5

Locked authority: DESIGN.md section 6 wins, HANDOFF.md, original brief and merged Day 1/2 main bc9cc75dbd78806ace0e6c4e953cc91e1e06e45b. Full ship lane for durable state, provider and credential boundaries. Native Codex medium implementation and separate general/contract/security reviewers; no T3 delegation/Claude calls. Established design carried forward, no new designer wave. Coordinator owns plan/issue/PR/memory and actual live calls; worker owns code/tests/prompts/schema/docs and focused offline proof. Max3 active agents incl root, one heavy host run.

## S3-day3-delivery executable contract

Inputs: existing public week/piece CLI and run_pipeline; --resume run directory; configured snapshot policies; current credential root; optional budget increases; advisor file configuration; injected offline interfaces and explicit --live verification script. Base has 134 tests and six-piece offline proof.

Outputs: resume reaches a terminal RunSummary using the same graph/checkpointer and saved inputs, without clearing trails/checkpoint/usage; typed advisor request/output/trail and advice.md/advice.patch; complete totals after advisor; real-provider reviewer failure proof and full six-slot sample under output/sample-week; README, ARCHITECTURE, limitations and verification evidence.

Ordering: startup validates all inputs/options and obtains exclusive run ownership before initializing clients or reconciling ledger. New runs snapshot inputs/config/instructions including advisor. Resume reads plan/settings/instructions from snapshot and SQLite state (not mutable live policy), resumes only unfinished nodes, completed resume makes no new model/search/image calls. Original thread identity persists, not inferred from a renamed directory. Export -> optional advisor -> final usage -> summary. Advisor error/budget/output failure recorded but does not alter shipped content/CSV. Resume cannot duplicate stage or usage records; stable IDs and next call_n preserve billed/uncertain prior calls.

Empty/error behavior: invalid/missing/incompatible snapshots/checkpoints or forbidden resume options refuse before calls/output mutation; zero shipped still exports headers/advisor status/summary. Existing finalized runs remain readable. Same-run simultaneous resume must fail closed before shared state writes. Crash-billed unsaved results can repeat; explicitly no exactly-once billing. Ledger releases only never-dispatched reservations and retains dispatched unresolved charges as uncertain. External counters already saved must survive resume. A crash within a subgraph must not replay checkpointed completed writer/reviewer/visual operations; tests interrupt actual graph boundaries.

Advisor: plain typed agent, file instructions, suggest-only. Reads saved run evidence and allowed snapshot instruction files. Writes human-readable recommendations and bounded unified diff against writer.md/rubric.md/inputs/brand-voice.md only. Code validates targets/source identity and derives patch from structured edits; never executes model code or applies patches, no automatic prompt weakening. No credentials, path escape, filesystem tool or new factual stats. Include advisor accounting in final summary and pure estimate. Optional disable control is documented; default follows DESIGN every-run advisor. Existing tests retained; any expected accounting change due added advisor is explicit and tests must still assert exact totals.

Live verification: coordinator only uses an available configured-provider credential (Gemini or locally configured OpenRouter), existing token/cost guard (default USD1 for one sample, separate small grounding budget); no credential values copied/printed. Normal CLI still configured-provider driven. Explicit --live harness uses real configured adapter, no fake approval. At least one real reviewer must reject deliberately unsupported claim with valid source ID, plus valid control. Then one actual six-slot sample from repository inputs, clear provenance/model/actual usage/cost source, all records/images/CSV inspectable. Real provider/search failure is captured honestly, not relabelled fake output or repeatedly spending until green. Any necessary transport compatibility/pricing correction gets focused prior-base red/green.

Sample and docs: commit complete sanitized sample including all required JSON trails/schema identity, accepted text, images/specs, plan and usage/summary; exclude SQLite/internal lock credentials/binary sensitive metadata as documented. Inspect content support, code gates and actual cards, label fictional demo statistics. Instructions match actual flags/providers/file paths and explain exact setup, third-type extension, tools/shared skills, graph/data flow, resume billing limits, cost estimate vs actual billing, 15 slots/second-platform growth and known limitations. KB owner approval remains outstanding; do not falsely claim owner review. Current source-first docs are independently reviewed, no invented capabilities.

Permissions: local worktree/run files and public research; coordinator bounded live configured-provider sample and grounding check within guard, no purchase/top-up, outbound posts/messages, production deployment or merge. Cloudflare deferred because credentials absent, Pillow/spec meets brief; document deferral (no claim built/live). No modifications outside worker-owned repo paths, no vault writes. Preserve all Day1/Day2 transport/security/quality fixes.

## Verification matrix

1. Real public CLI/Python resume missing on exact base, new tests red for intended absence then green current; restart actual checkpoint mid-research/write/review and verify skip/counts, artifacts unique, same approved text.
2. Completed resume zero-call, live policy edits ignored, preserved budget override and snapshot settings, identity survives folder rename; invalid/missing resume and concurrent ownership fail before calls/mutation.
3. Reserved recovery released vs dispatched uncertain retained; interrupted model calls use next IDs, confirmed usage never overwritten, external counts retained after restart.
4. Advisor successful typed output and grounded bounded diff, source files byte-unchanged; no accepted text/CSV edits; error/budget/malformed/path escape never sink run, summary last includes advisor actual/uncertain usage.
5. Existing 134 tests preserved (only explicit advisor fixture/accounting changes justified); six-slot pipeline, config startup, source guards, shared retry and all five Day2 regression findings still pass.
6. Pure estimate/help/config paths no API/search initialization including advisor; file-only third type still runs.
7. Actual installed/provider adapter compatibility plus pricing verified with primary docs/source; no live calls by worker/reviewers. Root live reviewer rejects unsupported claim and accepts control; budget status/usage recorded.
8. Complete six-slot live sample folder, all JSON validated, CSV byte matches approved numbered drafts, actual PNGs opened/inspected; no synthetic response or secrets hidden in committed sample.
9. README/ARCHITECTURE commands independently exercised, links and limitation claims checked against final source; diagram and two growth paths included, every deliverable traced to brief.
10. Independent general/contract/security CLEAN exact head; coordinator full pytest, pip check, temporary schema equality, diff check, live proof/sample provenance and secret scan. PR open unmerged; memory remotely saved.

Required acceptance: .venv/Scripts/python.exe -m pytest -q exit0; pip check0; schema export/equality0; public CLI --help/--estimate0; external offline resume/advisor redgreen/mutation proof; bounded root-only --live checks with literal pass/fail artifacts. No deployable service; deployment not applicable, publication not authorized. Day3 completion must distinguish built/offline verified/live judged/owner pending, and never call fake output live.
## Free-endpoint reviewer representation repair

User authorized free OpenRouter models after the paid endpoint credit failure. Live catalog verification pins a zero-priced `:free` ID; explicit existing `allow_unpriced` permits a token-only pre-call guard when the pinned price database lacks that ID. Record provider-reported cost and actual usage; never infer pricing from an arbitrary model suffix alone.

Inputs: configured rubric criterion keys, configured hard-fail keys and unchanged ReviewRequest. Output: an explicit per-request SDK wire schema converted back to the unchanged canonical ReviewAssessment before existing validation/scoring. Score keys/ranges and flag keys remain exact; reasons for criteria and true flags remain required, false-flag reasons may be omitted, extra string reasons and file-only third types retain their existing behavior. No threshold, retry limit, grounding rule or public artifact schema is relaxed. Installed SDK request/response callers must fail on the prior open-dictionary representation and pass with the explicit keys; live coordinator controls and sample remain separate required gates.

## Live source-coverage repair within S3

Actual Google Gemma4 31B default-profile controls pass; first six-slot run ships5, with missing thread rejected for STAT-01 not in its research pack. Root content inspection also finds approved unsupported marketing/technical expansions beyond terse supplied evidence. Preserve that run as failed/incomplete evidence. This repairs the existing grounding invariant, not approval thresholds.

Inputs: existing20KB schema, existing fictional brand approved_stats source mechanism, file-driven writers/reviewer and original post types. Enrich only KB-01/02/03 description/root_cause from their verified primary references with necessary mechanism/version/mitigation facts; retain20entries, identifiers, pending owner-review labels and explicit editorial severity limitations. Add a clearly fictional approved brand-service fact using the existing approved_stats mechanism; all fictional claims remain labeled. Do not invent achievements, clients, guarantees or measured effectiveness. Keep KB source construction and public schemas unchanged.

Outputs: grounded multi-tweet source material and strict writer/reviewer instructions that use only supplied pack IDs and evidence, without importing brand-only statistics into technical packs or treating background plausibility as support. Marketing writers must not add technical prevalence, process rigor, effectiveness or service claims absent evidence. Review each independently factual clause; plausible common knowledge is not supplied evidence. Preserve code validation, hard-fail unsupported_claim, thresholds, retry counts, all prior wire/metadata/secret fixes. A valid approved stat ID alone cannot ground unrelated claims.

Verification: independently source-check every added KB/brand fact against primary documentation or explicit fictional scope; source-first file checks and unchanged schema/code; one new real unsupported-general-claim control alongside existing supported/statistic controls, then fresh six-slot sample at final reviewed head. No artificial approval or copying accepted pieces between runs. Native Codexmedium implementation/independent review; root alone makes model calls and packages/inspects final artifacts.

## OpenRouter completion-error and explicit-none adapter repair

Actual new-key Dots controls at6dc produce a supported acceptance and general-claim rejection, but statistic control returns HTTP-success raw finish_reason=error without tool calls. Preserve controls as FAIL, not a semantic rejection. Root observed3000-token length exhaustion by reasoning, then error completions; no full Dots week is authorized before current controls and writer probe pass.

Inputs/outputs: keep configured client, canonical schemas, messages, scoring and budgets. After recording returned raw usage/settling its ledger entry, finish_reason=error is a provider failure, never valid output even if parsed exists. Retry with unchanged messages through the same bounded transient counter/backoff as transport errors (3 retries), then failed_api; do not spend the malformed-JSON repair opportunity or append repair feedback. Partial reported usage remains confirmed/estimated exactly as reported; unresolved transport errors remain uncertain. No extra retry layer or reset of counters.

For OpenRouter only, agent reasoning_effort=none must send the documented explicit reasoning.enabled=false gateway option through the installed ChatOpenAI request serializer; omitting the argument leaves model defaults active. Low/medium/high and other providers retain existing wire behavior. Preserve tool_choice/include_raw/schema/usage/privacy. Mandatory-reasoning endpoints may refuse an explicit disable; never silently enable against the operator setting. Do not use this option to bypass OpenCode client-only restrictions.

Verification: public ModelClient behavioral red on6dc and green onfix for error-with-parseable-output, shared bounded error retries/accounting/messages and exhausted failed_api; installed actual SDK serialization red/green proves OpenRouter none body plus unchanged non-none/native-provider controls without network. No mirrored string tests or fake live approval. Root independent general/contract/security review and full release before a fresh optional-none operator profile, three actual semantic controls, real writer probe and actual complete sample. Root alone makes model calls; no keys copied.

## S4 advisor instruction-target repair (compact lane, 2026-10-15)

Symptom: three live gpt-4.1-mini weeks finished with `advisor: failed`. A probe of the advisor alone on week 3 (`.runs/gpt41mini-week3-advisor-probe`, local evidence) shows every suggested edit used an evidence record id (`marketing_2/validator/2/1`) as `path` and tried to rewrite shipped post text. Code correctly refuses these (`InvalidAdvice: invalid advice source`), and fail-closed policy then discards all recommendations.

Invariant (unchanged): the advisor is suggest-only and fail-closed; `advice_patch` validation, numeric guards, machine-rule guards and tests stay as they are. Shipped content is never edited.

Fix: `agents/advisor/system.md` states that `edits[].path` must be one of the keys of the request's `instructions` map and `source_sha256` its `sha256`; evidence record ids, slot ids and shipped or draft text are never edit targets; observations about shipped text (for example an encoding glitch) belong in `recommendations`. A public-interface offline test proves the instruction reaches the advisor's system message and that an edit targeting an evidence record id still fails closed with `failed_output`.

Acceptance: focused pytest for the new test, then the full suite; root then reruns the advisor probe live and confirms `advisor: completed` on a fresh live week.

## S5 KB-04..20 evidence enrichment (compact lane, 2026-10-15)

Symptom: a live week shipped 6/6 but the coordinator content audit found approved unsupported claims (thread_2 on KB-06: "drain funds", "immutable", "verified on-chain"; thread_1: "often occur"). KB-04..20 carry about 50 characters each, while a tech thread needs at least five tweets, so writers pad beyond evidence. Free reviewer models also accepted the padding, so the fix belongs at the evidence source. Kassra chose this fix on 2026-10-15.

Invariant: KB schema, ids, names, severity, category, aliases and the review_status label are unchanged; entries stay `pending_kassra_review`; severity remains an editorial label. Code, prompts, thresholds and tests are unchanged.

Fix: rewrite `description` and `root_cause` of KB-04..20 to the depth of KB-01..03 (mechanism, a concrete example where the source gives one, version or chain conditions, mitigation), using only facts stated in the entry's primary references (SWC Registry entry, Solidity documentation, EIPs, OWASP Smart Contract Top 10). A reference may be added only if it is a primary source actually read. No statistics, prevalence words, incident names or monetary figures. Outdated SWC behaviour is stated with its version or chain condition (for example constructor naming before Solidity 0.4.22, selfdestruct after EIP-6780). A claim ledger outside the repo maps every added sentence to a quoted source line.

Acceptance: kb.json loads through `KnowledgeBase`, the full suite passes, an independent reviewer spot-checks the claim ledger against the sources, then a fresh live week on a free OpenRouter model is audited by hand.

## Length feedback and advisor failure label (c1df937, compact lane, 2026-10-15)

Live gpt-4.1-mini weeks dropped marketing posts at 293-445 weighted characters after the single content retry because the writer only received `tweet_N:length`. The validator node now appends writer-only measurements (`weighted_length`, overshoot, word count, `max_words` = limit / 7.5) to feedback, and the marketing writer budgets by words. Violations themselves are unchanged. Advice that code refuses now records `failed_output` instead of `failed_api` (DESIGN 6.10); fail-closed policy is unchanged. Independent Opus review of c1df937..fd608e7: CLEAN, red on base and green on head (2026-10-15).

Open low findings, queued as S6 for a Sonnet worker: measurement notes also land in a dropped slot's summary `reasons` (free-form list, beyond DESIGN 2.6 wording); `max_words` floors to 0 below a limit of 8; no tests for a multi-tweet numbered thread, a lowered limit, or a non-InvalidAdvice advisor error still mapping to `failed_api`.

## S6 review follow-ups for length feedback (compact lane, 2026-10-15)

Implementer: Codex gpt-6.1-sol, medium (Kassra, 2026-10-15). Fix: a dropped slot's summary `reasons` exclude the writer-only `weighted_length` notes (DESIGN 2.6: validator violations plus reviewer reasons); `max_words` is at least one; tests cover a multi-tweet numbered thread measurement, a lowered limit, and a non-InvalidAdvice advisor ValueError still recording `failed_api`. Acceptance: red on base for the reasons and floor tests, focused tests green, full suite green.

## S7 provider-agnostic credential lookup (compact lane, 2026-10-15)

Ask (Kassra, 2026-10-15): switching providers should be a LangChain `provider:model` string plus a key in `.env`, not new code per provider. Today `LangChainTransport.prepare` refuses any provider missing from a hard-coded key-name map, and picks the structured-output method from a second hard-coded map, so Groq cannot run even with `langchain-groq` installed.

Invariant: existing providers behave exactly as before (same key names, OpenRouter/OpenCode base URLs and reasoning handling, Gemini thinking options, structured-output methods); `.env` values are never logged or written; a missing or blank key still raises `missing credential for selected provider`.

Fix: keep the explicit key-name overrides (`google_genai` -> `GEMINI_API_KEY`, and the current entries); any other provider whose name matches `^[a-z][a-z0-9_]*$` reads `<PROVIDER>_API_KEY` (for example `groq` -> `GROQ_API_KEY`); a provider name outside that pattern raises `unsupported configured model provider`. Providers without an explicit structured-output method use `function_calling`. Add `langchain-groq` to `pyproject.toml` dependencies and pin it in `requirements.txt`.

Acceptance: a public-interface offline test through `LangChainTransport.prepare` with a faked `init_chat_model` shows `groq:<model>` reads `GROQ_API_KEY`, passes it as `api_key`, and requests `function_calling`; it fails on the base with `unsupported configured model provider`. Existing provider tests stay green; full suite green. Root then runs a live free-model probe on Groq.

## S7b preflight accepts any well-formed provider (compact lane, 2026-10-15)

Symptom: after S7 (72f8d84), `live_proof --model groq:openai/gpt-oss-120b` exits 2 with `live_proof_error:ValueError` before any model call. Confirmed cause (offline repro `.runs/s7p-diag/repro_groq_preflight.py`): `validate_inputs` in `content_pipeline/pipeline.py` raises `invalid configured model reference` because it keeps its own hard-coded provider set; an OpenRouter model passes the same preflight. S7's acceptance tested only `LangChainTransport.prepare`, not the run's public preflight, so the second allowlist was missed.

Invariant: the reference pattern, the OpenRouter `/` rule and every other preflight check are unchanged; existing providers still pass; malformed provider names still fail before any client is built.

Fix: one shared provider-name rule. `content_pipeline/model_client.py` exposes the S7 pattern (`^[a-z][a-z0-9_]*$`) as a single helper used by both `LangChainTransport.prepare` and `validate_inputs`, replacing the hard-coded provider set in `pipeline.py`.

Acceptance: an offline test through `validate_inputs` with a `groq:` model fails on 72f8d84 with `invalid configured model reference` and passes after; a malformed provider and an OpenRouter reference without `/` still fail; full suite green. Root then reruns the Groq live sample.

Open low findings from the S7 Opus review (not fixed here): F2 Groq/Gemini connection and timeout errors are not retried and record `unknown`; F3 Groq HTTP 400 `tool_use_failed` ends as `failed_api` without a repair attempt. F1 (reasoning_effort rejected on qwen) was disproved by a live call.

## S7c run-wide reasoning-effort override and Mistral (compact lane, 2026-10-15)

Symptom: Kassra needs the final sample today; free OpenRouter (50 requests/day), Gemini (20/day) and Groq (8,000 TPM) cannot finish a week. Mistral's free plan allows `ministral-14b-latest` at 937,500 TPM and 30 RPM. A real structured call through `LangChainTransport` (`.runs/s7p-diag/smoke_provider.py`) succeeds at effort `none` but returns HTTP 400 `reasoning_effort is not enabled for this model` at `low` and `medium`, and every agent config sets low or medium. `--model` already overrides every agent's model; there is no matching override for effort.

Invariant: without the override, every agent keeps its configured `reasoning_effort` exactly as today, including Gemini thinking handling; reviewer, thresholds, retries and the grounding control are unchanged (the grounding control still has to PASS before the sample runs).

Fix: `Settings.reasoning_effort_override` (`none|low|medium|high`, default unset; env `REASONING_EFFORT_OVERRIDE`) replaces each agent's `reasoning_effort` in `configured_client` when set. `live_proof` gets `--reasoning-effort` (applied to the grounding control and the sample, and recorded in `sample-provenance.json` and the grounding report); `cli` gets `--reasoning-effort-override`, refused on resume like the other overrides. Add `langchain-mistralai` to `pyproject.toml` and pin `langchain-mistralai==1.1.6` plus its new transitive pins in `requirements.txt`.

Acceptance: an offline test shows the override reaches `LangChainTransport` for every agent and that without it agent efforts are unchanged; it fails on the base. Resume refuses the new flag. Full suite green. Root then runs the live sample with `--model mistralai:ministral-14b-latest --reasoning-effort none --allow-unpriced`.

## S9 validator safe fix removes banned characters (compact lane, 2026-10-15)

Symptom: live Mistral `ministral-14b-latest` week at 56a0b7c (`.runs/final-56a0b7c-mistral`): grounding control PASS, zero API failures, 1/6 shipped; thread_1 and thread_3 were dropped only for `banned_character:—`, and second attempts still carried em dashes after validator feedback.

Decision (Kassra, 2026-10-15): mechanical brand rules are enforced in Python in the validator node before review, so a model's punctuation habit cannot sink a post. Invariant: the banned_characters list, check_brand, banned words, hashtags, links, length, citations, the reviewer, thresholds and retries are unchanged; check_brand still runs after the fix and still rejects anything left.

Fix: a new safe fix `banned_characters` (added to the `BrandRules.safe_fixes` literal and listed first in `inputs/brand-voice.md` `safe_fixes`) runs before the other fixes: an em dash — with any surrounding whitespace becomes ", "; the non-breaking hyphen ‑ becomes "-"; any other configured banned character is removed. It is recorded as `tweet_N:banned_characters` in `fixes`, like `curly_quotes`. Length is measured after the fix.

Acceptance: offline tests through `validate` show a draft whose only violation was an em dash is accepted with the fix recorded and the text rewritten, the non-breaking hyphen becomes "-", an over-length draft is still rejected for length, and a brand file without the fix in `safe_fixes` still rejects the em dash; they fail on 56a0b7c. Full suite green. Root reruns the Mistral live week.

## S10 readability (compact lane, 2026-10-15)

Ask (Kassra, 2026-10-15): the code is hard to read; the brief evaluates "Code and docs: easy to read and easy to run". Phase A (1f33cd0): `ruff format` at line length 100 across all Python files, AST-identical (`.runs/s10-ast-check.py`), suite green. Phase B: hand refactor of the hardest product modules, one module per slice, starting with `content_pipeline/pipeline.py`, then `model_client.py`, then `scripts/package_sample.py`.

Invariant: pure refactor. Behaviour, public names and signatures imported by tests or other modules, file formats, trail contents and log lines are unchanged; existing tests are not edited; the full suite stays green and ruff format --check stays clean.

Phase B style: a one-line docstring on every public function and class; descriptive names instead of one- or two-letter locals; long functions split into named helpers in the same module; no nested comprehension chains or semicolon statements; module-level constants for magic values; lines at most 100 characters.

Acceptance per module: full suite passes unchanged before and after; independent review confirms behaviour preservation by reading the diff and rerunning the suite.

## S11 agent prompt rewrite (compact lane, 2026-10-15)

Ask (Kassra, 2026-10-15): Opus rewrites agent prompts that are too thin or badly written. Author: Opus 5.5 coordinator; reviewer: Codex.

Evidence: two mixed-model weeks at d4dda99 dropped marketing_3 (education angle) at 378-395 weighted characters twice; the writer had been asked for hook, mechanism, mitigation, CTA and a fictional label within 37 words. marketing_1 repeated "fictional" four times and the Mistral reviewer rejected it citing a non-existent prohibition and scored a service post down for having no technical detail. Both rubrics were 43 words with no score anchors and no hard-fail definitions; research, visual and skill prompts were single run-on notes written for developers.

Change: rubrics gain per-criterion 1/3/5 anchors and definitions of unsupported_claim and off_brand (a fictional/demo label is required, never off-brand; a service post with no technical claim is not penalized on technical_accuracy). The marketing writer gets a three-sentence recipe per angle; the education angle uses the mechanism OR the mitigation, not both; the fictional label is used once. Writers are told the banned_characters list and (marketing) exactly one tweet. Reviewer, research, topic, visual, writer system and skill prompts are restructured into headed sections or short lists with the same rules.

Invariant: no rule is removed or weakened: grounding, citation, fictional-label, hard-fail, length and evidence rules keep their meaning; every phrase pinned by existing tests is kept verbatim; rubric criteria front matter and type.yaml are unchanged; code is unchanged.

Acceptance: full suite green unchanged; independent review compares old and new prompts rule by rule; one final live week at the reviewed head.
