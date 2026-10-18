---
criteria: [hook, technical_accuracy, grounding, voice, clarity, cta]
---
Score each configured criterion from 1 to 5 with a concrete reason that points to the text. Check every factual claim against exactly the supplied evidence, including uncited claims. Unsupported claims and off-brand language fail closed. Explain every rejection.

What the scores mean: 5 is ready to post as it is, 3 is acceptable but plain, 1 fails the criterion.

- hook: 5 when the first tweet states a specific vulnerable pattern or contrast that makes a developer want to read on; 3 when clear but generic, or when it opens with a question; 1 when vague or clickbait.
- technical_accuracy: 5 when mechanism, conditions and mitigation match the evidence exactly, keeping compiler versions and chain conditions; 3 when correct but simplified in a way that changes no claim's truth or conditions; 1 when wrong or misleading. Dropping a version or condition that the evidence attaches to a claim makes that claim broader than its evidence: score 1 and set unsupported_claim. Read every code span as a reviewer would: a call missing an argument (an ERC-20 `transfer` with no recipient), code that would revert or not compile in the library it names, or code from one project presented as working in another scores 2 or lower.
- grounding: 5 when every factual clause traces to the cited evidence_text; 3 when one minor wording stretch adds no new fact; 1 when any fact, statistic or source is invented.
- voice: 5 for the calm, precise engineering voice of the brand voice; 3 when slightly promotional or loose, or when the same authority is credited in several tweets ("OWASP says" again and again); 1 for hype, unsupported certainty, banned wording, or text that points at the research material ("the article's example", "from the discussion").
- clarity: 5 when each tweet makes one point and the thread builds from hook to mechanism, example, mitigation and a closing check; 3 when understandable but repetitive; 1 when the order is confusing or jargon is left unexplained. A tweet that announces code or a list it does not contain scores 2 or lower.
- cta: 5 when the thread ends with a concrete check or next step the reader can apply, supported by the evidence; 3 for a generic closing; 1 for no closing, or a closing that adds an unsupported claim.

Hard-fail flags:
- unsupported_claim: true when any factual clause is not supported by the supplied evidence, or drops a version or condition the evidence attaches to it, even if every other clause is.
- off_brand: true for a banned word or phrase, hype, a promise of complete protection, metadata leaked into the published text (source ids, field names).
