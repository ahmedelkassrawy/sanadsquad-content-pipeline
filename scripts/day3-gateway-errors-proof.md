# Day 3 gateway completion error / explicit none proof

Issue #5, S3-day3-delivery; compact implementation under the newest
`plans/5-day3-delivery.md` adapter contract. Arrival dispatch checker PASS at
`ed3e24fff7bf872a277ca49083d723f1be505297`.
Reproduction base: `6dcf26757c65147caa38b1ce2ed6169ade87779d`.

## Diagnosis and bounded change

Observed provider artifacts in external `day3-newkey-dots-controls/observed-reviewer`
include a raw `finish_reason=error`, no tool calls or parsed result, and returned
usage (call4:2156 input +1300 output). Previously the raw error reached malformed
output repair. A valid tool payload accompanied by that error could even be
returned as successful output. These are provider failures, not semantic rejection.

`ModelClient.call` now classifies error after ledger settlement and usage recording,
before truncation or parsing/output validation. It consumes the existing shared
transient counter with1/2/4 second backoff, unchanged messages and cap, then raises
`failed_api` after three retries. JSON repair and truncation opportunities remain
separate; neither is consumed/reset by the error. Existing pricing and
confirmed/estimated/uncertain accounting are unchanged.

`LangChainTransport.prepare` supplies `extra_body.reasoning.enabled=false` only for
OpenRouter with configured effort `none`. The installed ChatOpenAI and OpenAI SDK
serialize it as top-level `reasoning: {enabled: false}`. Existing non-none effort
serialization and other-provider routing remain unchanged. No fallback enables
reasoning against the setting, and no OpenCode restriction is bypassed.

Primary documentation verified 2026-10-14:
https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
documents the reasoning object, `enabled`, disabling via effort `none`, model
reasoning defaults and mandatory-reasoning endpoint refusal. Its distinction
between hiding reasoning and disabling it is relevant: this change disables it.
No claim that a live provider honored the option follows from local SDK proof.

## Behavioral verification matrix

| Scenario, real caller | Archived6dc | Fixed working files |
| --- | --- | --- |
| Installed SDK error without tool output, then valid result | Appends erroneous repair feedback | Unchanged request retried, actual usage/cost/reasoning retained |
| Installed SDK error with valid parsed tool output | Incorrect payload returned | Payload never reaches output validator; later valid result returned |
| Four parseable error completions | Incorrect success | Four charges; delays1/2/4; `failed_api` |
| Timeout/error/timeout/error mixture | Wrong `failed_output` path | Shared3retry ceiling; confirmed vs uncertain charges retained |
| Error, length, malformed, error, valid output | Incorrect repair exhaustion | One truncation and one repair retained; no error feedback mutation |
| Error without usage followed by reported success | Wrong repair/no backoff | Estimated confirmed first usage; reported confirmed second usage |
| Installed OpenRouter SDK none serialization | Missing gateway setting | Actual HTTP JSON contains explicit disable |
| OpenRouter low/medium/high, native OpenAI none/medium, OpenCode none/medium | Seven controls pass | Same seven controls pass, real tool result/usage preserved |

All HTTP uses `httpx.MockTransport`; actual LangChain/OpenAI request serialization,
response parsing and public `ModelClient`/ledger interfaces run. Test credentials
are synthetic, with a fresh empty credential directory, so no local `.env` is read.
No model calls, full suite, nested agents, defaults, prompts, thresholds, retry caps,
or public schema changes are part of this slice.

## Commands and preserved evidence

From project root with project venv:

```powershell
& ./.venv/Scripts/python.exe scripts/prove_day3_gateway_errors.py --evidence-dir <new-external-directory>
```

Driver exit0. It archives exact6dc, copies only the new test, runs Python `-S` with
explicit package/site paths and verifies import containment. It records exact
commands/cwds/exits, source hashes and UTF8 logs. Archived baseline exits1 with
**7 intended failures,7 passing controls**; fixed files exit0 with **14 passed**.
Source hashes tie the precommit working-tree run to committed product/test bytes.

Preserved external directory:
`C:/Users/Dell/.t3/scratch/2026-10-12-i-want-to-write-the-c75c9f5b/day3-gateway-errors-redgreen`
contains `report.json`, `base-red.txt`, `working-green.txt` and the archived source.

Focused previous guards, exit0, **61 passed**, one existing pkg_resources warning:

```powershell
& ./.venv/Scripts/python.exe -m pytest tests/test_day3_gemini.py tests/test_day3_review_wire.py tests/test_day3_review_wire_nonnull.py tests/test_review_regressions.py tests/test_foundation.py::test_structured_repair_truncation_and_provider_cost tests/test_foundation.py::test_malformed_reviewer_fails_closed_and_bills_both tests/test_foundation.py::test_transient_retries_retain_uncertain_charge_and_sanitize_error tests/test_foundation.py::test_permanent_provider_budget_failure_does_not_retry tests/test_foundation.py::test_budget_refusal_precedes_dispatch tests/test_foundation.py::test_usage_estimate_and_actual_overshoot -q
```

`git diff --check` exits0. Existing tests were not edited.

Independent review, coordinator release suite, fresh actual controls/writer/week,
PR integration and vault save remain pending with coordinator. No deployment or
merge performed; local mock proof is not live provider acceptance.
