---
criteria: [hook, technical_accuracy, grounding, voice, clarity, cta]
---
Score each configured criterion from 1 to 5 with a concrete reason that points to the text. Check every factual claim against exactly the supplied evidence, including uncited claims. Unsupported claims and off-brand language fail closed. Explain every rejection.

What the scores mean: 5 is ready to post as it is, 3 is acceptable but plain, 1 fails the criterion. A marketing post is one tweet built around one supported point.

- hook: 5 when the opening is a specific statement, contrast or question tied to the post's angle and evidence, and the post follows through on it; 3 when clear but generic, or when the post never answers its own opening question; 1 when vague or clickbait. A statement opening is as good as a question; do not lower the score for not asking one.
- technical_accuracy: 5 when every technical statement matches the evidence exactly; 3 when correct but simplified in a way that changes no claim's truth or conditions; 1 when wrong or misleading. Dropping a version or condition that the evidence attaches to a claim makes that claim broader than its evidence: score 1 and set unsupported_claim. A service or credibility post that makes no technical claim scores 5 here when it implies none; do not penalize it for lacking technical detail.
- grounding: 5 when every factual clause traces to the cited evidence_text, and a firm fact is used only for what it says; 3 when one minor wording stretch adds no new fact; 1 when any fact, statistic, capability or result is invented. Wording that is more specific than the evidence (a count, size or outcome it does not state, for example "twice" where it says "again") adds a fact: score 1 and set unsupported_claim.
- voice: 5 for the calm, precise engineering voice of the brand voice; 3 when slightly salesy; 1 for hype, unsupported certainty or banned wording.
- clarity: 5 when the post reads as one finished thought in two or three short sentences; 3 when understandable but cluttered or repetitive (for example the same label repeated); 1 when it is a list of facts or hard to follow.
- cta: 5 when it ends with one direct next step or engagement question that the evidence supports (requesting a review only when the pack describes that offering); 3 for a weak or generic ending; 1 for no next step, or one that adds an unsupported promise.

Hard-fail flags:
- unsupported_claim: true when any factual clause is not supported by the supplied evidence, or drops a version or condition the evidence attaches to it, including prevalence words such as "most" or "often", firm capabilities, rigor or results.
- off_brand: true for a banned word or phrase, hype, a promise of complete protection, metadata leaked into the published text (source ids, field names).
