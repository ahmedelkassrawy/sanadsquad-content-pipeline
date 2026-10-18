# Free run, 6 of 6 shipped (2026-10-18)

A copy of the readable files and images from a live run on the free setup: Ministral 14B
writers (`mistralai:ministral-14b-latest`) and a Nemotron 3 Super reviewer
(`nvidia:nvidia/nemotron-3-super-120b-a12b`, reasoning effort low), with
`--set thread_2="ERC-4626 vault inflation attack"` so research had to search the web.
It used 145,524 tokens for $0.0199 in LLM cost.

It is here to show the quality gates working on a weaker model. All six pieces shipped,
and three of them only after a rejection and their one retry:

- `thread_1`: code rejected `recipient.call`, which X would turn into a link. The writer
  was told how to rewrite it and the retry passed review.
- `marketing_1`: the reviewer rejected "detailed" audits as more specific than the
  evidence (grounding 1, `unsupported_claim`); the retry passed.
- `marketing_3`: code rejected the claim words "ensure" and "prevent", which the evidence
  does not use. The retry deleted them and passed review.

The same free models shipped 3 of 6 before these code checks existed; see
`output/free-run-with-drops`, which also shows what a drop looks like. Start with `run.log`.

Only these files were copied (no checkpoint or snapshot), so this folder cannot be resumed
or re-exported. The main sample is `output/sample-week`.
