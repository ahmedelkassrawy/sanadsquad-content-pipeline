# Verification and limitations

Offline tests exercise the public Python runner, CLI, actual graph interrupts, SQLite
checkpoints, OS ownership refusal, lifetime ledger recovery, advisor success/failure,
the five Day 2 repair families, provider SDK errors and real PNG/spec files. Injected
responses prove code routing and persistence, not live semantic grounding.

`scripts/prove_day3.py` archives merged Day 2 `bc9cc75`, copies new caller regressions
and asserts package import containment with Python -S and explicit installed packages.
It measures missing resume/advisor and incompatible Gemini reasoning requests, then
tests the current head, resume/advisor guard-bypass mutants and exported schema equality.
Evidence directories must be new and outside the repository; the script does not alter
tracked source. Release checks and independent review remain coordinator-owned.

Gemini compatibility was checked with installed langchain-google-genai and the actual
google-genai AsyncModels producer intercepted before network. Structured JSON requests
and SDK-shaped responses verify raw usage includes thinking. The adapter maps 2.5
effort to bounded budgets: low256, medium768 and high1536, each capped at one quarter
of max_output_tokens, subject to model minimums. Flash none0; Pro none128. Output caps
too small for a required minimum refuse before dispatch. Newer Gemini retains effort
levels. Bundled `genai-prices` recognizes the configured Google alias; no unpriced bypass
was introduced. Primary references: [Google Generate Content thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking)
and [LangChain Gemini adapter](https://reference.langchain.com/python/langchain-google-genai/chat_models/ChatGoogleGenerativeAI).
Installed source and caller tests establish the particular pinned producer behavior.
LangGraph's [persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence)
explains thread/checkpoint and subgraph namespaces; actual interrupted-run assertions
establish this repository's recovery behavior.

The coordinator separately measured a real configured Gemini 2.5 Flash reviewer control
on the initial corrected adapter: a deliberately invented statistic with a valid KB-01
ID passed code citation validation, then the reviewer rejected it with unsupported_claim;
a supported KB control was accepted. Reported actual usage was 5197 tokens with
USD0.0078423 list-price estimate and no uncertain charge. This is one bounded judgment
check, not universal factual accuracy or actual provider invoice proof. Reuse requires
unchanged reviewer/prompt/rubric/evidence/adapter inputs. The separate real DDGS/public
fetch probe retrieved five official EIP pages with usable page evidence; it does not
prove every future search succeeds. The six-piece live sample is coordinator-owned and
must carry its actual provenance; no injected six-slot run is labeled live here.

The first real six-slot attempt on the initial whole-run head exported zero pieces:
all six writer drafts passed code validation, but reviewer transport calls failed.
The retained run reported 8079 confirmed tokens/USD0.0078555 estimated list price and
35038 uncertain reserved tokens/USD0.0567114. A bounded diagnostic replay observed
GoogleAPIError503 and exhausted its USD0.02 guard after two uncertain attempts.
This is provider failure, not quality approval or a successful sample. Separately,
offline SDK429 tests established the installed typed rate-limit wrapper drops its
direct code; typed/cause handling restores bounded retries without logging provider
exception text. That repair does not prove the failed live attempt was a429 or that
the service recovered. Further live execution belongs to the coordinator after review.

After the file prompt explicitly required reason keys for every true hard-fail flag,
the committed live harness passed both controls on Gemini 2.5 Flash Lite: the supported
claim was accepted and the unsupported claim was rejected with unsupported_claim.
The coordinator measured 5412 actual tokens/USD0.0014115 list-price estimate and no
uncertain charge, with request/review/usage artifacts and source hashes retained.
This supersedes no history: an earlier Lite response safely became failed_output for
missing a required hard-fail reason, and the earlier Flash sample remains an API failure.
The current bounded judgment check does not certify every generated claim.

A subsequent Lite six-slot attempt shipped one marketing piece and failed the other
five slots; totals were 16925 confirmed tokens/USD0.0041045 estimated list price and
156836 uncertain reserved tokens/USD0.0480836. That marketing text incorrectly included
literal source_ids bookkeeping. The run is retained as failed evidence, not the final
sample. Writer instructions now place bookkeeping only in structured fields, and the
demo brand rules reject literal source_ids before review/export, with a public caller
regression. The coordinator owns any later bounded sample attempt and content inspection.

The final safe SDK metadata probe confirmed Gemini Flash Lite free-tier daily quota
exhaustion: the observed project/model limit was20 requests and RetryInfo requested
34735 seconds. This is a particular account observation, not a universal provider limit.
The complete live sample remains pending provider availability. No further blind Gemini
week, synthetic substitution or weakened acceptance contract is used to claim completion.

Primary-source review supports the 20 concise KB core facts/reference assignments.
SWC is historical/incomplete. KB-15 qualifies inherited-state shadowing as legacy
Solidity; KB-02/05 include current compiler/SELFDESTRUCT references. Editorial severities
and categories are not official ratings. Kassra owner review is still pending.
Brand statistics remain deliberately fictional demo fixtures.

Known limits: checkpoint windows can repeat billed work; unsaved external counters can
be lost; local pre-call estimates can overshoot billing; USD covers LLM only; no Cloudflare
adapter or scheduler-specific import/publishing is built. A sample package cannot resume.
The pinned twitter-text parser emits the documented pkg_resources deprecation warning;
setuptools is pinned below81 for compatibility. Live adapter coverage differs by provider;
supporting an adapter does not claim every model/account combination was called live.

The independent contract review found an advisor guard defect: a new percentage claim
could borrow12 from unrelated fictional-audit front matter. Each edit now rejects added,
removed or modified numeric-bearing full lines, including changed words around an existing
number omitted from the edit excerpt. A numeric no-op and a real nonnumeric clarity edit remain allowed.
Public-run regressions preserve shipped text/source bytes and isolate rejected advice.
This conservative rule does not recognize the meaning of number words or certify prose;
all recommendations still require human review before manual application.

The subsequent real OpenRouter GPT-4.1-mini grounding attempt on5916999 ended failed_budget
for both controls, with8086 uncertain reserved tokens/USD0.0104344 estimated exposure,
not measured actual billing. The key authenticated, but the credits endpoint reported
zero total credits; calls stopped. Provider availability remains pending either a quota
reset or available credits. No complete real six-slot sample is claimed.

Independent security review found recursive sample packaging could copy synthetic private
extras. Packaging now requires the saved snapshot manifest/hash identity AND the exact
file set derived from its saved type/agent configuration. It rejects unexpected files,
links/junctions/reparse points, source/destination overlap, altered snapshot bytes and
invalid/mismatched referenced PNG/spec files before destination creation. Only validated
bytes are copied, never recursive directories. Public tests generate six-slot offline
PNG and spec-fallback runs; those controls prove packaging, not live provenance.
