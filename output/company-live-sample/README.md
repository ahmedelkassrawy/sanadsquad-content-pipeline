# Live web search sample (2026-10-24, commit b136cc0)

The readable files and images of a live week on the committed models (Sonnet 5.5 writers, Opus
5.5 reviewer) with real web search (Tavily), run at commit `b136cc0` with
`python run_week.py --plan plan.json --output <run>`. It shipped 3 of 6 for $0.675 and 203,844
tokens; Tavily answered 4 searches.

What it shows, in `run.log`:

- Live, allowlisted research. Each marketing slot searched only its service line's domains and
  fetched real pages; `research_log.json` holds every query, backend attempt and URL.
- `marketing_3` (news reaction, web3 security) shipped on its first draft. It reports Zellic's
  audit-drift findings on the Nomad hack and links the page it was given, which was fetched in the
  run from an allowlisted domain.
- The format rotation: this week's marketing formats are thread, short tip and news reaction, and
  the planner adjusted `marketing_2` to a short tip so the week has a post without a link.
- `thread_2` (SS-03699) and `thread_3` (SS-03897) explain their findings without naming the
  audited projects.
- What the gates dropped: `thread_1` twice by the reviewer for pointing at the source ("In this
  audit finding"); the x402 marketing thread first for having one post instead of three to seven,
  then for a tweet opening with a label ("Root cause first:"); the vulnerability-class post for
  missing a citation on each draft. The last one led to a fix after this run (`922b54f`): the
  check now names every missing citation at once.

Start with `run.log`. `posts.csv` holds the three shipped pieces. Only these readable files and the
three shipped images were copied (no checkpoint or snapshot), so this folder cannot be resumed or
re-exported.
