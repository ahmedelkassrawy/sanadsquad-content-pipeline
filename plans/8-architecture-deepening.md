# Architecture deepening, visual fallback and Cloudflare backend - issue #8

Ask (Kassra, 2026-10-15): do all six candidates of the architecture review of main `b59c24b` (improve-codebase-architecture; text in "Candidates" below), fix the visual fallback and build the Cloudflare image backend. Divide the work between Codex, Claude and OpenCode DeepSeek 4.1 Flash on max effort through T3 delegation.

Locked authority: DESIGN.md (section 6 wins), the task brief, this plan, main `b59c24b`. Full ship lane: cross-module architecture, budget and ledger accounting (S15), a new external API with a credential (S18). Coordinator: Claude Opus 5.5 (plan, packets, integration, merges into `feat/8-architecture-deepening`, PR, memory). Implementers by T3 delegation: Codex `gpt-6.1-sol` medium (S15, S19, S20), Claude Opus 5.5 (S17, S18), OpenCode `deepseek-v4.1-flash-free` max (S16, S21, S22). Reviewers are never the author's model: Opus 5.5 reviews Codex and DeepSeek slices, Codex reviews Claude slices; S18 also gets a security review. Active agents: three implementers plus the coordinator (user-authorized override, 2026-10-15); reviews run in waves.

Rules for every slice:
- Work only in the assigned worktree and owned paths. Other workers edit other slices of the same files in parallel; never revert or reformat code you do not own.
- Run tests with `D:/me/SanadSquad-day3/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider` from the worktree root (the package is imported from the working directory). Ruff: `D:/me/SanadSquad-day3/.venv/Scripts/python.exe -m ruff check . && ... -m ruff format --check .`.
- Pure refactor slices: behaviour, trail and file formats, log lines, failure kinds and public names imported by tests are unchanged; existing tests are not edited except to replace a hard-coded literal by the new module's call where the slice says so; the full suite passes before and after.
- Behaviour slices: new tests call real interfaces (`run_pipeline`, `ModelClient.call`, `validate`, node functions) with fakes only at the true external seams (transport, HTTP); they must fail on the base for the intended reason and pass on the fix.
- No live model, search or image calls, no reading `.env`, no installs, commits or pushes, unless the slice says so. The coordinator commits verified diffs.
- Code reads like the surrounding code: one-line docstrings on public functions, descriptive names, blank lines between logical steps, lines at most 100 characters, `ruff format` clean.
- Worker report starts with a summary of at most 10 lines: verdict, files, commands with exit codes, red-on-base evidence, open questions.

## S15 model call module (Codex)

Problem: `ModelClient.call` (model_client.py 552-887) interleaves estimate, reservation, dispatch, retry, pricing, repair and ledger settlement. Two confirmed bugs sit in it. Any exception after dispatch keeps the worst-case reservation as uncertain spend (602-603), including a provider HTTP error response such as a 429, 400 or 401, which carries a definite answer from the provider. Every 403 raises `failed_budget` (632), although `_failure_for_status` already classifies 403 as `auth`.

Change: keep `ModelClient.call`'s signature and its caller-visible results (parsed output or `ModelFailure(kind)`) the same. Inside the module, make one per-attempt outcome decision (settle, release, keep uncertain, retry, repair or fail) in one place; build the uncertain `UsageRecord` in one helper; fold the two copied repair blocks (801-841, 843-883) into one; honour a provider `Retry-After` header (seconds, capped at the existing maximum delay) for retryable statuses when present.

Accounting rule (DESIGN 6.7 clarification, Kassra 2026-10-15): an exception that carries an HTTP status from 400 to 499 other than 408 is a definite provider answer, not an unknown result: release the reservation and record the call with `status: "confirmed"`, zero tokens, zero cost, its `failure_kind` and `http_status`. Timeouts, connection errors, 408, 5xx, cancellation and errors with no HTTP status stay uncertain exactly as today. 402 still ends the slot `failed_budget`; 401 and 403 end it `failed_api`; a budget refusal by our own ledger is unchanged.

Owned paths: `content_pipeline/model_client.py`, `content_pipeline/ledger.py`, `content_pipeline/models.py` (only if a field is needed), new `tests/test_model_call_outcomes.py`, DESIGN.md 6.7 (one bullet for the rule above).

Acceptance: new tests through `ModelClient.call` with a fake transport: a 429 then success releases the first reservation and the run's uncertain total is zero; a 403 ends with `ModelFailure("failed_api")` and no uncertain spend; a 402 still ends `failed_budget`; a timeout still keeps an uncertain charge; a `Retry-After: 2` 429 waits 2 seconds (fake sleep). Each fails on the base for the intended reason. Full suite green.

## S16 review scoring module (DeepSeek)

Problem: the review contract is checked three times: `review_wire_schema` (review_wire.py), `review_contract_errors` and `score_review` (agents.py 41-131). A rubric rule change must be made in up to three places.

Change: one module `content_pipeline/review_scoring.py` owns building the wire schema from the rubric and judging one assessment: accept, reject with reasons, or malformed. `Reviewer` (agents.py 332-359) calls it. `review_wire.py` and the two agents.py functions are removed or become one-line re-exports so existing imports keep working. Pure refactor: the same assessments produce the same decisions, reasons, failure kinds and trail records; fail-closed behaviour (DESIGN 6.10) is unchanged.

Owned paths: `content_pipeline/review_scoring.py`, `content_pipeline/review_wire.py`, the `score_review`, `review_contract_errors` and `Reviewer` parts of `content_pipeline/agents.py`, new `tests/test_review_scoring.py`.

Acceptance: full suite green before and after with existing tests unedited; new tests through the module's public judgement show accept, reject with reasons, and malformed for missing keys, out-of-range scores and identity mismatch.

## S17 visual fallback (Claude)

Symptom: in the submitted sample (`output/sample-week`, run at `9e19ac2`), all six visual calls returned `finish_reason: tool_calls` with confirmed usage, yet five of six cards used `fallback_spec` with `fallback_reason: "visual_fallback"`. `_visual_spec` (pipeline.py 1047-1066) catches every exception and records no detail, so the cause is not observable.

Step 1, diagnose before fixing: rebuild the six visual requests from `D:/me/SanadSquad-day3/.runs/final-9e19ac2-claude` (snapshot plus drafts and research) and run `VisualAgent` with the run's visual model `mistralai:ministral-14b-latest`, reasoning effort none, at most 12 calls in total. This is a free model; the key is read by the existing client from `D:/me/SanadSquad-day3/.env` (never print it). Record per slot whether the agent raised or returned a spec, and the `overlay_errors` list. Save the evidence under `.runs/s17-diagnosis/` in the worktree (not committed).

Step 2, fix (adjust to the evidence and say so in the report):
- Record the reason: `ImageResult` gains an optional `fallback_detail: list[str]` with the agent exception class or the `overlay_errors` codes; `fallback_reason` values stay as they are.
- Grounding compares overlay text to the accepted text and sources after normalizing case, whitespace and quotes, so a correct excerpt is not rejected for capitalization; numbers and brand checks are unchanged.
- One repair attempt: when the returned spec has overlay errors, call the visual agent once more (attempt 2, billed through the budget) with the error codes as feedback; only then fall back. Malformed output keeps its existing single repair (DESIGN 6.10).
- The visual prompt states the exact-excerpt rule with a short example.
- DESIGN 6.5 records the repair attempt and the detail field.

Owned paths: `content_pipeline/images.py`, the visual part of `content_pipeline/pipeline.py` (`_validate_saved_visual`, `_visual_spec`, `_render_visual`, `visual`), `content_pipeline/agents.py` `VisualAgent` only, `content_pipeline/models.py` `ImageResult` and visual request models only, `agents/visual/`, new `tests/test_visual_fallback.py`, DESIGN.md 6.5.

Acceptance: tests through the visual node with a fake visual model: a spec whose overlay differs from the accepted text only by case ships with `fallback_reason: null`; a spec with an ungrounded overlay gets one repair call and ships the repaired spec; a second failure falls back with `fallback_detail` naming the error; an agent exception records the exception class. Each fails on the base. Resume of a saved visual from before the change still validates. Full suite green.

## S18 Cloudflare image backend (Claude, after S17 in the same worktree)

Design (DESIGN 2.9, now built): `config/visual.yaml` `backends: [cloudflare_flux, pillow_card]` is honoured in order. `cloudflare_flux` calls Cloudflare Workers AI `@cf/black-forest-labs/flux-1-schnell` (`POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell`, JSON `{"prompt": ..., "steps": 4}`, response `result.image` base64) with credentials `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` read like other keys (environment or `.env`). The prompt is built by code from the spec's `concept`, `scene` and palette and tells the model to draw no text. The image is cropped and resized to `CARD_SIZE`, then the same Pillow overlay step draws the overlay text and optional severity badge on it with a readable backing band. No credentials: skip to the next backend silently with `backend: "pillow_card"` and no fallback reason. Any Cloudflare failure (HTTP error, timeout of 60 seconds, invalid image, decode error): fall back to `pillow_card` with `fallback_reason: "cloudflare_failed"` and `fallback_detail` naming the failure kind and HTTP status, never the token or response body. Calls are counted in the existing image counters (`image_calls`, new `cloudflare_images`), not priced (DESIGN 6.7). The token never appears in logs, trail, exceptions or the summary. A resumed run validates a saved `cloudflare_flux` PNG like a Pillow card. The live harness is not changed.

Owned paths: new `content_pipeline/image_backends.py`, `content_pipeline/images.py`, the visual part of `content_pipeline/pipeline.py`, `content_pipeline/config.py` (visual config loading only), `config/visual.yaml`, `.env.example` (Cloudflare lines), `pyproject.toml` and `requirements.txt` only if `httpx` must be declared, new `tests/test_cloudflare_backend.py`, DESIGN.md 2.9.

Acceptance: tests through the visual node with `httpx.MockTransport`: with credentials and a valid base64 PNG/JPEG response the slot ships `backend: "cloudflare_flux"` and a 1200x675 PNG whose overlay text is drawn (pixel difference from the raw image in the text area); without credentials no HTTP request is made and `pillow_card` ships; a 401, 500, timeout and invalid base64 each fall back to `pillow_card` with the detail and the token absent from every written file and log. Each fails on the base. Live proof (one real image) is pending Kassra's Cloudflare credentials.

## S19 record id module (Codex, after S15 in the same worktree)

Problem: the stable id `slot_id/node/attempt/call_n` (DESIGN 6.6) is built by f-strings in pipeline.py (673, 697, 780, 866, 933, 965, 1125, 1165, 1187; literals 798, 1219, 1249, 1302) and agents.py (231, 256), built and parsed by `rsplit` and a ledger key prefix scan in model_client.py (542, 564-577), and depends on `safe_name` elsewhere.

Change: new `content_pipeline/record_ids.py` owns building and reading the id (record id and call id, next call number from existing ledger keys). All callers use it. Pure refactor: every id string is byte-identical to before.

Owned paths: `content_pipeline/record_ids.py`, id construction lines in `pipeline.py`, `agents.py`, `model_client.py`, new `tests/test_record_ids.py`. Existing tests may replace hard-coded id literals only by calls to the new module where that keeps the asserted value identical; prefer leaving them.

Acceptance: full suite green before and after; new tests for build/parse round trip, ids containing unusual slot names, and next call number after a resumed ledger.

## S20 stage replay module (Codex, after S19 in the same worktree)

Problem: "can I reuse this saved stage?" is answered in eight places: `saved_record`, `saved_research`, `_validate_research_replay`, `research_request_digest`, `save_research_receipt`, `_validate_saved_visual`, the `input_sha256` writes, `_completed_checkpoint` (pipeline.py), `resume_inputs` called three times per resume (cli.py 81, pipeline.py 1418, 1564), a silent duplicate drop in `Trail.append` (trail.py 18-19) and a status rewrite on ledger load (ledger.py 18-22). Drafts have no input fingerprint and the "saved stage input mismatch" tamper paths have no test.

Change: new `content_pipeline/stage_replay.py`: every lane node (research, writer, review, visual, advisor) asks one interface whether a saved stage record can be reused; it reuses a record only when its input fingerprint and the run snapshot match and otherwise refuses, one way. `resume_inputs` is computed once per resume and passed down. `Trail.append` skips an id it already has only when the content is identical; a different record with the same id raises (DESIGN 6.6 records this). Run folders written before this change (including `output/sample-week` and `.runs/final-9e19ac2-claude`) still resume.

Owned paths: `content_pipeline/stage_replay.py`, the replay parts of `pipeline.py`, `durability.py`, `trail.py`, `ledger.py` load path, `cli.py` resume path, new `tests/test_stage_replay.py`, DESIGN.md 6.6.

Acceptance: tests through `run_pipeline --resume` style entry points: a tampered saved review, visual and draft input are each refused with "saved stage input mismatch" (red on base for the draft, which has no fingerprint today); a duplicate trail id with different content raises; a completed resume makes zero model calls; an existing old-format run folder copy resumes. Full suite green.

## S21 skill modules (DeepSeek, after S16 in the same worktree)

Problem: `skills/brand_rules.py`, `skills/search_kb.py` and `skills/web_search.py` have no callers; the logic sits in `validation.check_brand`, `research.KnowledgeBase`/`WebSearch` and duplicate wrappers in `tools.py`. The brief asks for shared skills; deleting the three files changes nothing today.

Change: each skill's `.py` file becomes the implementation its `.md` describes. `skills/brand_rules.py` owns the brand check that `validation.validate` and `images.overlay_errors` call; `skills/search_kb.py` and `skills/web_search.py` own the functions that `tools.research_tools` exposes to the research loop, so the duplicates in `tools.py` go. Existing import paths (`validation.check_brand`, `research.KnowledgeBase`, `research.WebSearch`) keep working as re-exports. Pure refactor: identical results, violations and tool outputs. DESIGN 6.4 / 2.3 states that each skill is one module with two faces (prompt text and code).

Owned paths: `skills/*.py`, `content_pipeline/tools.py`, `check_brand` in `content_pipeline/validation.py`, the import line in `content_pipeline/images.py`, `research.py` only for re-exports, new `tests/test_skill_modules.py`, DESIGN.md 6.4.

Acceptance: full suite green before and after; new tests show the validator, overlay check and research tools call the skill modules (monkeypatching the skill function changes their result), and the shim files are no longer callerless.

## S22 post type module (DeepSeek, after S20 is merged)

Problem: "what does this post type mean?" has no single home: `load_type`, `load_agent` (globs every `type.yaml`) and `load_prompt` in config.py are re-run per lane node (research x2, write x4 with 3 globs, validate, review x2 with 3 globs, visual with 3 globs, `load_brand` at 3 call sites), and the kb-vs-angles topic source is branched in research.py, pipeline.py and agents.py.

Change: new `content_pipeline/post_types.py` loads post types, their agents, prompts and brand rules once per run from the snapshot and answers per slot type: writer, rubric, prompts, topic pool, research pack assignment. Nodes and agents ask it. The kb-vs-angles rule lives in one place. Pure refactor; DESIGN 6.9's acceptance (a third post type as folders only, no Python change) still holds and is tested.

Owned paths: `content_pipeline/post_types.py`, loading call sites in `config.py`, `pipeline.py`, `agents.py`, `research.py`, new `tests/test_post_type_module.py`.

Acceptance: full suite green before and after; a test adds a third post type as folders in a temp copy and runs it through `run_pipeline` with fakes; a test shows YAML is parsed once per run (counting loader calls).

## S23 docs (coordinator: Sonnet 5.5 writes, Opus 5.5 reviews, medium)

README, ARCHITECTURE.md, DESIGN.md and `.env.example` match the assembled head: the new modules, the Cloudflare keys and fallback, the visual repair attempt, and the accounting rule. `/doc-truth` discipline: a cited claim list first.

## Integration

The coordinator merges each reviewed slice into `feat/8-architecture-deepening` in this order: S15, S16, S17, S18, S19, S20, S21, S22, S23, resolving overlapping hunks in pipeline.py and agents.py without dropping either side, and reruns the full suite and ruff after each merge. One combined integration review on the final head, then one PR closing #8. No live pipeline run is part of this issue; the committed sample stays as it is.

## Candidates (from the architecture review, 2026-10-15, main 28d8e07)

1. Deepen the model call module (Strong). 2. One stage replay module for resume (Strong). 3. Make the skill files the implementation, or delete them (Strong). 4. One post type module per run (Worth exploring). 5. Give the record id one home (Worth exploring). 6. Fold the three review checks into one review scoring module (Speculative: the duplication is deliberate fail-closed defence; Kassra chose to do it anyway). Line numbers above are from main `b59c24b`.
