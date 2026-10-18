# Free run, 6 of 6 shipped (2026-10-18)

A copy of the readable files and images from a live run on the free setup, on the same code
as the main sample: Ministral 14B writers, research and visual
(`mistralai:ministral-14b-latest`) and a Nemotron 3 Super reviewer
(`nvidia:nvidia/nemotron-3-super-120b-a12b`, reasoning effort low), with
`--set thread_2="ERC-4626 vault inflation attack"` so research had to search the web.
It used 178,047 tokens (163,464 confirmed plus 14,583 uncertain) for $0.0204 in LLM cost and
took about 5 minutes.

It is here to show the code gates working on a weaker model. All six pieces shipped, and
three of them only after a rejection and their one retry:

- `thread_2`: code rejected the outline labels "Example:" and "Mitigation:"; the retry
  rewrote them as sentences and passed review.
- `marketing_1`: the reviewer rejected "clear fix" as more specific than the evidence's
  "recommended fix" (grounding 1, `unsupported_claim`); the retry passed.
- `marketing_2`: code rejected the claim word "precise", which the evidence does not use.
  The retry deleted it and passed review.

The code checks catch the same faults on any model, but the free reviewer is lenient: it gave
all 5s on most final reviews, including thread_1, which uses the same withdrawal example in
tweets 2 and 3. A stronger reviewer model is the next lever.

Each thread got its own hook and closing style from `post_types/tech-thread/type.yaml`, so
the three threads open and close differently.

Earlier free runs on older code shipped 3 of 6 (see `output/free-run-with-drops`, which also
shows what a drop looks like). Start with `run.log`.

Only these files were copied (no checkpoint or snapshot), so this folder cannot be resumed
or re-exported. The main sample is `output/sample-week`.
