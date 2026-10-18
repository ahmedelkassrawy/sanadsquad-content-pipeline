# Run folder

Start with `posts.csv` and `run.log`.

| File | What it holds |
| --- | --- |
| `posts.csv` | The deliverable: accepted posts (Post Text, Image) for a scheduler. |
| `run.log` | What happened to each slot, step by step, and the totals. |
| `run_summary.json` | Shipped, dropped and why, tokens and cost. |
| `plan.resolved.json` | The plan after --set overrides. |
| `research_log.json` | Every search and the evidence each slot was given. |
| `drafts.json` | Every draft, the code fixes applied and any rule violations. |
| `review_report.json` | Reviewer scores and reasons for every draft that reached review. |
| `final.json` | The accepted pieces with topic, sources and image. |
| `posts.json` | posts.csv as JSON. |
| `visuals.json` | Image specs and how each image was made. |
| `images/` | One image (and its spec) per accepted piece. |
| `ledger.json` | Token and cost totals per slot and agent. |
| `usage.jsonl` | One line per model call: model, tokens, cost. |
| `external-usage.json` | Search, fetch and image calls. |
| `advisor.json` | Optional advisor output: suggested instruction edits. |
| `snapshot/` | Copy of the inputs and instructions this run used. |

Internal resume files (checkpoint.sqlite, state.json, run_identity.json, runtime-settings.json, research-replay.json, run.lock) are not listed.
