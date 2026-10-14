# Empty content before numbering — issue #1

Regression base `773a036be8165d79efc17049b1596ee9eaf09fd2`; measured fixed product `b3a16ac8d281adc6ed0c8fa005220cfd5ab2735a`. The later evidence commit changes only these proof files.

The validator now checks that safe-normalized body text is nonempty before adding numbering. A trailing hashtag block occupying the entire body is removed consistently with a trailing block after prose. Empty bodies produce `tweet_N:invalid_text` once. Original drafts and fix logs remain intact; final numbered text still goes through full brand/parser/weighted-length checks.

`.venv\Scripts\python.exe scripts/prove_empty_content.py red`: driver exit **0**, archived old-package pytest exit **1**, **4 failed / 1 passed**. Intended failures cover all-blank thread, a blank unit among nonempty thread units, and hashtag-only bodies with numbering enabled/disabled. The positive control retains full numbering-before-weighted-length rejection. The archive is the exact base and `-S`/explicit module paths plus an import-location assertion prevent editable-head leakage.

`.venv\Scripts\python.exe scripts/prove_empty_content.py green`: driver exit **0**, focused pytest exit **0**, **5 passed**; full `.venv\Scripts\python.exe -m pytest -q` exit **0**, **73 passed**, one documented upstream parser warning. `git diff --cached --check` exited **0** before committing. Exact commands, cwd, head and outputs are retained in `empty-content-proof.json`.

Only validator normalization, its focused regressions and proof files changed. Models/schemas, dependencies and other reviewed surfaces are unchanged, so their earlier evidence was reused. No live API requests, spending, graph expansion, vault or PR operations occurred. Independent final contract/security review and coordinator release verification remain pending.
