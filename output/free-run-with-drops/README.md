# Free run with drops (2026-10-18)

A copy of the readable files from a live run on the free setup: Ministral 14B writers
(`mistralai:ministral-14b-latest`) and a Nemotron 3 Super reviewer
(`nvidia:nvidia/nemotron-3-super-120b-a12b`, reasoning effort low). It took about 3 minutes.

It is here because it shows the paths the main sample does not: 3 of 6 shipped. The
reviewer rejected `marketing_1` and `marketing_2` twice and dropped them for claims the
evidence does not support, and dropped `thread_2` after its retry. Start with `run.log`.

Only these files were copied (no checkpoint, images or snapshot), so this folder cannot be
resumed or re-exported. The main sample is `output/sample-week`.
