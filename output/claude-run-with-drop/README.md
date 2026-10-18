# Claude run with a drop (2026-10-18)

The readable files and images of a live week on the committed models (Sonnet 5.5 writers, Opus
5.5 reviewer), run with the same command as `output/sample-week`. It shipped 5 of 6 for $0.666.

It shows the reviewer doing the job the brief asks for. In `run.log`:

- `thread_2` (ERC-4626) was rejected twice for a worked example that does not add up: both drafts
  copied the source's own "101-token balance", while the thread's numbers give about 201. After
  its one retry it was dropped, not shipped.
- `thread_3` was rejected for crediting OWASP in several tweets and for one claim the evidence
  does not support; the retry shipped.
- `marketing_2` was rejected for joining two stats into a new fact ("12 audits completed by a
  team of 3 auditors"); the retry shipped.

This run used commit 5dd7f4a. The next run, after one added writer rule (on an arithmetic
rejection, recompute rather than restate the source's figure), is the committed
`output/sample-week`. Only these files were copied, so this folder
cannot be resumed.
