# Day 2 pipeline — issue #3

Canonical requirements: DESIGN.md (section 6 wins), HANDOFF.md, reviewed Day 1 head 27c40d29e7f13f6fd70f985f22a4afdbb100ed81. Full ship lane for new graph/state/contracts, credential use and output writes. Carry the settled design forward; no new designer wave or ADR reversal.

Dependency: PR #2, still unmerged. Day 2 branch feat/day2-pipeline starts at its exact reviewed head, and its PR targets feat/day1-foundation. Do not merge either PR or deploy. User explicitly authorized proceeding to Day 2; two dependent slices are started, only Day 2 is being implemented. Max three active agents including coordinator; one full host suite at a time.

## Executable slice S2-day2-pipeline

Inputs: root/configuration, validated plan slots (one or more; sample has six), output directory, CLI focus/settings overrides, typed research/writer/review/visual requests, injectable model/search interfaces. Agent constructors can read their file instructions; each call accepts one Pydantic request and returns one Pydantic contract. No agent sees WeekState.

Outputs: resolved plan, evidence/research log, original/normalized drafts, code validation and rubric reviews, accepted-only image/spec files, final content, posts.csv/posts.json in plan order, usage/ledger and terminal summary. Per-run records carry slot/attempt/stable record IDs. JSON schemas cover all serialized contracts.

Ordering:
1. Load/validate plan/types/CLI --set and configuration before model calls; --estimate does not call model or search APIs.
2. Focused slots research in parallel; one batch choice for empty slots, deterministic fallback by topic_source and historical exclusion. Every chosen topic resolves to actual usable evidence; failures stay slot-local. Research alone uses search_kb/web_search and a bounded tool/action loop (max four searches per slot), with retained SourceRecords.
3. LangGraph fans out independent slot lanes via Send. Thin graph nodes map typed state to plain agent calls. Writers are separate configured instances from type.yaml and never search. Writer -> code validator -> reviewer; invalid drafts skip reviewer. At most two content drafts (original + one feedback retry) irrespective of validator/reviewer rejection mix. Model transport retries/repair remain separately bounded.
4. Reviewer reads identical evidence plus file rubric; code computes pass average/required hard-fail policy rather than trusting a model approval boolean. Missing/invalid rubric output fails closed, rejection explains why. Review does not edit drafts. Unsupported claims are a rubric hard failure.
5. Accepted pieces only go to Visual. Visual agent returns ImageSpec; code checks overlays against brand rules and accepted/source text, no new stats. Optional severity comes from evidence/KB and type config. Visual error or budget refusal creates a deterministic grounded spec. Pillow renders locally; render failure writes a spec JSON referenced in Image. All paths stay within the run directory.
6. Join exactly once after all slots terminal; export accepted pieces in original plan order, unchanged approved normalized/numbered text, including header-only CSV when none ship. Summary records all terminal states/reasons, confirmed/uncertain tokens/cost and external-call counters/scope. No advisor in this slice.

Errors/empty behavior: configuration/unknown --set slot errors occur before calls; failed research/API/output/budget slots do not prevent other lanes/export/summary. No citable evidence means failed_research. Reject mismatched slot/attempt outputs and malformed/underspecified approvals. Empty plan is invalid; empty accepted set is valid output. Preserve existing output runs rather than overwrite their artifacts; each new run has its own folder. Safe slot/file names; never serialize API keys or .env into snapshots/logs.

Persistence: integrate SQLite checkpointer as designed, save serializable state and input/config snapshots. The --resume flag itself remains Day 3. Do not claim exactly-once API billing. Concurrency/reducers must merge by slot ID without lost updates or duplicate export.

Public interfaces: run_week.py --plan <path> --output <dir> --set <slot=focus> [--estimate/settings flags]; run_piece.py --type <type> --topic <focus> [--output <dir>]. Both call the same orchestrator. Test callers may inject fake model/search factories through Python entry points; normal CLI uses configured providers. No fixture silently presented as real review or live proof. No live requests/spend in this implementation slice.

Permissions: project-local environment/code/output writes, public research only; no keys logged/copied, publication/scheduling/email, production access, merge or deployment. User's worker/reviewer choice is native Codex medium; Opus 5.5 only for genuinely new design. Coordinator owns issue/plan/vault/PR; worker preserves others' edits.

Dependency versions: pin resolved LangGraph/SQLite/Pillow additions in requirements; verify primary docs and installed producer behavior. Keep Day 1 transport/security fixes and all original 73 tests intact. No LiteLLM.

## Verification matrix and owners

Worker implements/focused verifies; independent general, contract and security reviewers rerun their proof; coordinator owns assembled release suite and visual inspection.

1. Six-slot public run with fake structured transport/evidence: exports six pieces, real PNG files, all JSON trails/summary/schema-valid data; explicitly offline fixtures.
2. Two focused research tasks and at least two write lanes overlap (barrier/event proof), each result reaches its own slot; single export/summary.
3. Validator rejection skips reviewer; reviewer rejection supplies feedback; exactly one shared content retry, second failure drops; rejected pieces never get visuals.
4. Source/API/budget failure in one slot leaves others shipped; malformed/unsupported/missing-score review fails closed; zero shipped header-only CSV and complete summary.
5. --set parsing, plan-order output, startup refusal/no calls for invalid config; --estimate no calls; run_piece uses same graph and produces one-piece run folder.
6. Empty topics distinct/fallback/historical exhaustion; known aliases and unknown focus web evidence; tool loop capped; no source fabrication or writer search.
7. Visual LLM/budget/Pillow errors fall back to grounded spec; banned/new-stat overlays cannot ship; Windows/traversal paths rejected; PNG text fits canvas and images inspected.
8. Add third post type entirely through files and execute it through the graph/CLI interface without Python edits.
9. Concurrent trails/usage/ledger/checkpoint and snapshots preserve data, state/source identity and secret exclusion; all stage records carry trace IDs.
10. Baseline new public pipeline/CLIs absent on exact Day 1 base; guard-bypass negative control rejects tests when validator/reviewer gate removed. Full pytest, pip check, schema regeneration/comparison and diff checks pass on reviewed assembled head.

Acceptance commands: project .venv/Scripts/python.exe -m pytest -q (all existing/new tests, exit 0); schema export and pip check exit 0; public CLI --help/--estimate exit 0; independent mocked whole-run proof plus actual Pillow PNG inspection. Record exact head/base, numeric exits and proof artifacts. No live-quality claim from scripted reviewers.

Pending Day 3: --resume, optional advisor/Cloudflare, real provider/semantic grounding smoke, final sample and README/ARCHITECTURE/limitations. KB owner review still pending. Day 2 completion means reviewed offline pipeline, not finished submission or authorized deployment.
