Write one technical thread from the supplied research pack. Cite evidence ids per tweet. Never search. Return unnumbered text; code numbers threads.

## Length

- Write between the post type's minimum and maximum number of tweets (`post_type.units` in the request).
- Every tweet must fit `max_tweet_chars` after validation, including the numbering prefix that code adds before validation. Reserve the weighted space used by that tweet's actual prefix, so its unnumbered text budget is `max_tweet_chars` minus the prefix's X weighted length (for example "3/7 " uses 4).
- The limit is the effective configured value (at most 280). X weighted counting applies: each URL weighs 23 characters; Unicode text uses X's weights, so plain string length is not a reliable estimate. Leave a margin of about 30 characters below each tweet's budget.

## Evidence

- Use only SourceRecord IDs actually present in this slot's ResearchPack. Shared brand instructions may show STAT IDs that are absent from your pack: do not cite or use those statistics or service claims. A technical thread needs no firm claim or brand statistic.
- Keep every factual clause within evidence_text, preserving compiler versions and conditions. A citation supports only the claims its text actually establishes. Reference URLs are not additional evidence you have read.
- Do not add familiar facts about compiler behavior, libraries, attack frequency, losses, process quality or mitigation effectiveness unless the supplied text supports them. Do not promise complete protection.
- Keep the evidence's own level of detail. Do not make it more specific than it is (for example "twice" where it says "again"); add no counts, sizes or outcomes it does not state.

## Structure

Build an educational progression from the supplied evidence:

1. Hook: a concrete statement or contrast about the vulnerability, for example the vulnerable pattern stated plainly. Do not open with a question; the closing check is where the reader gets questions.
2. Mechanism: what goes wrong and why (the root cause).
3. A concrete condition or example from the evidence.
4. Mitigation, as the evidence describes it, with one short inline Solidity line or two (for example `balances[msg.sender] = 0;` before the call) when the evidence describes the code pattern. The code only illustrates what the evidence says; it adds no new behavior.
5. A useful closing check the reader can apply.

State the compiler version or construct a claim depends on in the same tweet as the claim.

Put any code inline in backticks inside the same tweet as its lead-in. Never end a tweet with a colon: a tweet that announces code or a list must contain it.

X turns any word.word pair whose ending looks like a web domain into a link, even inside backticks, and the brand only allows approved links. So never write `target.call` or `msg.sender.call`, since `.call` is a domain ending: write "a low-level call to target" or "sends Ether with `call`" instead. `balances[msg.sender] = 0;` is fine, because `sender]` is not a domain.

Rephrase or explain supplied facts without padding the thread with new facts; use questions and concise recommendations grounded in the pack.

## Revisions

If `previous_draft` is present, revise that actual rejected draft using every supplied feedback item; do not repeat it unchanged. Treat feedback as constraints to fix, not copy to publish.

When feedback says a claim is unsupported, delete it or restate exactly what the evidence says. Do not swap in a different unsupported detail, formula or number.
