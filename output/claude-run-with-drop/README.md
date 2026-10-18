# Claude run with a drop (2026-10-19, commit 440ab3c)

The readable files and images of a live week on the committed models (Sonnet 5.5 writers, Opus
5.5 reviewer), run with
`python run_week.py --plan inputs/plan.json --output <run>` at commit `440ab3c`
(`fix/eval-round-4-polish`). It shipped 5 of 6 for $0.503.

It shows every gate the brief asks about working in one run. In `run.log`:

- `thread_1` (reentrancy) and `thread_2` (denial of service with failed call): both first
  drafts were rejected for reordering evidence sentences nearly verbatim and for repeating an
  earlier point (`voice` and `clarity` at 2). Both retries shipped, with the same minimum score
  rising from 2 to 4.
- `thread_3` (transaction order dependence): draft 1 never left review — the reviewer set
  `unsupported_claim` because tweet 7 added an effectiveness conclusion the evidence does not
  state. Draft 2 never reached the reviewer: the code's credit rule hard-failed it for crediting
  SWC in two tweets (`repeated_credit:swc`), so the slot was dropped. With its one retry used
  and no acceptable draft, the run continued without it.
- `marketing_1`, `marketing_2` and `marketing_3` shipped on their first attempt.

Start with `run.log`. `posts.csv` holds the five shipped pieces; `review_report.json` holds the
three first-attempt rejections with the reviewer's reasons. Only these readable files and the
five shipped images were copied (no checkpoint or snapshot), so this folder cannot be resumed
or re-exported. The main sample is `output/sample-week`.
