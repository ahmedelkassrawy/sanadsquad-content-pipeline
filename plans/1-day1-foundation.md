# Day 1 foundation — issue #1

Canonical requirements: DESIGN.md (section 6 wins), HANDOFF.md. Full ship lane: typed contracts, credentials and outbound research calls. Locked architecture is carried forward; no new design wave.

Scope: implement the Day 1 foundation only. Package `content_pipeline` owns contracts, config loading, validation, pricing, model calls, ledger and research skills. Repo-level `agents/`, `post_types/`, `skills/`, `inputs/`, `config/` hold file instructions and fixtures. No orchestration/full-week CLI capability is claimed in this slice.

Inputs: six-slot JSON plan; markdown brand front matter; 18–20 reviewed-source KB entries; post-type YAML; environment settings; injected fake/real chat transport.
Outputs: validated Pydantic records, JSON schemas, retained source evidence, original and normalized drafts, usage journal, deterministic assignment results and bounded errors.
Ordering: normalization and numbering precede validation; reviewer-facing evidence is identical to writer-facing evidence; ledger reserves before dispatch and settles after response; unknown dispatched charges remain uncertain.
Empty/error behavior: invalid config fails startup; missing evidence fails research; missing topic assignments fall back by topic_source; nonexistent citations fail validation; malformed structured output gets one repair; no keys required for offline tests.
Permissions: no secrets printed or committed; no publishing, scheduling or messaging; research only fetches public HTTP(S) evidence; project-local environment only.
Dependencies: install project venv, verify primary docs/installed signatures; pin resolved versions in lock or requirements. Never use LiteLLM. Agent model choice: Codex gpt-6.1-sol medium; no Claude except Opus 5.5 for genuinely new design.

Owners: implementation worker owns code/config/tests; coordinator owns plan, issue, vault and verification queue; independent Codex reviewers own general/security/contract verdicts. Preserve all others' edits.

Acceptance: `.venv/Scripts/python.exe -m pytest -q` exits 0; `.venv/Scripts/python.exe -m content_pipeline.schemas --output schemas` exits 0 and exported schemas match models. Record baseline missing-capability failure on 438120a, then green on head. Reviewers rerun required checks independently.

Verification matrix (offline unless noted):
1. Input/config/schema loading: six slots, unknown types and limits >280 rejected.
2. Brand normalization/numbering/weighted length/links: exact accepted text and violations.
3. Citations: unknown, failed and snippet-only evidence rejected.
4. Topic assignment: missing/duplicates recovered by kb or angles; explicit duplicates warn; history exhaustion logs.
5. KB retrieval: aliases retrieve correct retained entry; writers have no lookup tools.
6. Web skill: injected search/fetch results preserve evidence; failed fetch never citable; no live-network claim from fixtures.
7. Model wrapper: billed success/repair/truncation/transient/permanent failures have bounded attempts and recorded usage.
8. Budget: concurrent reservations cannot oversubscribe; dispatched unknown charges retained; unknown pricing fails unless opt-in.
9. Trail/export primitives: concurrent records aren't lost and CSV round trip retains quoted multiline content if implemented now.
10. Required-path negative control: bypass citation/brand guard causes acceptance assertions to fail.

Pending beyond this slice: agents/graph/full CLIs, real sample run, KB owner review, optional images/advisor/resume, final submission README/ARCHITECTURE. PR is reviewable; merge and deployment require separate authorization.
