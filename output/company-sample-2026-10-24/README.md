# SanadSquad content sample: one live week, 2026-10-24

Six slots, live web search, generated on `main` at `03267f8`. All six shipped. One slot
(`thread_3`) was re-drafted separately after a naming fix; everything else is from the single
week run.

**What produced it**

- Research, writing and visuals: Claude Sonnet 5.5. Reviewer: Claude Opus 5.5, a different model.
- Search: 5 live Tavily queries. Every page had to sit on the slot's service-line allowlist, and
  every linked page was fetched during the run.
- Images: 6 Cloudflare FLUX cards.
- Cost: $0.84 for the week (262,263 tokens) plus $0.15 for the `thread_3` re-draft (35,881
  tokens), so $0.99 in total.

**The posts**

| Slot | Type | Topic | Shape | What happened |
| --- | --- | --- | --- | --- |
| `thread_1` | tech-thread | SS-03738, non-canonical Pyth price feeds | 9 posts | Reviewer rejected draft 1 for reusing evidence sentences; draft 2 shipped. |
| `thread_2` | tech-thread | SS-03756, missing ownership check for `RegisteredProgram` | 8 posts | Code gates rejected draft 1 (rule-of-three, section label); draft 2 shipped. |
| `thread_3` | tech-thread | SS-03804, permissionless account partially DoSes a multisig | 8 posts | Re-drafted (see below). |
| `marketing_1` | marketing-post | Web3 security: audit coverage | service overview | Zellic/Rekt figure on unaudited hacks, the Nomad audit gap, CTA allowed. |
| `marketing_2` | marketing-post | Agentic payments: x402 spending limits | short tip | No CTA. |
| `marketing_3` | marketing-post | Agentic security: vulnerability class | news reaction | The one marketing angle allowed to cite the knowledge base; links the OWASP cheat sheet. |

**Why `thread_3` was re-drafted**

The week's first `thread_3` named the multisig framework behind SS-03804. The audit client was
not named, but the framework name plus the instruction name leads straight to the public report,
so it was treated as a leak. The client and framework names are now on
`inputs/redacted-names.txt`; the slot was re-run alone on the same finding, and its second draft
shipped. `posts.csv` and `posts.json` use the re-drafted text; `logs/` keeps both runs.

**Files**

- `posts.csv`, `posts.json`: the six accepted pieces.
- `images/`: one image per piece, with its spec.
- `sources.md`: the sources behind each post; every web link was fetched in the run.
- `logs/run-week.log`: slot-by-slot outcomes and totals for the week.
- `logs/run-thread-3-redraft.log`: the re-draft.

**Caveats**

- The reviewer is an LLM. It accepted these drafts; the scores and reasons are in the logs. They
  are drafts for editorial review, not final copy.
- `marketing_3` uses a SanadSquad finding because the vulnerability-class angle is the single
  exception to the knowledge-base rule; it cites that finding.
