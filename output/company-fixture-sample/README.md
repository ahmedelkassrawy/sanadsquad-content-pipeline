# Company requirements sample (2026-10-24, commit 43d5151)

The readable files and images of a live week on the committed models (Sonnet 5.5 writers, Opus
5.5 reviewer), run at commit `43d5151` with `SEARCH_BACKEND=fixture` and
`python run_week.py --plan plan.json --output <run>`. It shipped 5 of 6 for $0.529.

**Search was answered from fixtures, not the live web.** With `SEARCH_BACKEND=fixture`, each web
search returns entries from `inputs/search-fixtures/`. Each entry pairs a real page URL on an
allowlisted domain (OWASP, OpenZeppelin, Coinbase x402 docs) with a short hand-written summary of
that page. The marketing posts quote those summaries, not the pages' own text, so a line such as
"OpenZeppelin's docs say review should cover initialization and upgrade paths" is the fixture's
paraphrase. A live run (Tavily or DDGS) fetches the real pages instead.

What it shows, in `run.log`:

- Source separation. The three threads are built from knowledge-base findings (`thread_1` found
  its finding with `search_kb`; the other two were assigned one); the three marketing posts
  research only their service line's allowlisted domains (`web_search`), and none of them reads
  the knowledge base.
- Every query is logged, including the ones that returned nothing (`marketing_3` ran three
  searches that found nothing before it finished). `research_log.json` holds each query, the
  backend and every URL event.
- Protocol names stay out. `thread_1` (SS-03738) and `thread_3` (SS-03926) explain their findings
  without naming the audited project; `thread_1` names Pyth, the price oracle, which is not the
  audited project.
- The AI-text checks run before review. `thread_2` (SS-03793) was dropped by code after two
  drafts that each held two three-item lists (`ai_text:rule_of_three`); it never reached the
  reviewer.
- Marketing formats and the weekly mix. The top of `run.log` shows each marketing slot's format
  (short tip, learning resource, differentiation), service line and CTA allowance, then every
  weekly-mix rule's verdict on the viable and shipped sets. The short tip carries no link; the
  learning resource links the OWASP page it was given.
- Retries. `marketing_1` first failed a code check (two brand mentions), `marketing_2` was
  rejected by the reviewer for an unsupported causal claim, and `marketing_3` first ran over
  length. All three shipped on their second attempt.

Start with `run.log`. `posts.csv` holds the five shipped pieces. Only these readable files and
the five shipped images were copied (no checkpoint or snapshot), so this folder cannot be resumed
or re-exported.
