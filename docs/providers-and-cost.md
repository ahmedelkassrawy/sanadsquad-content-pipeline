# Providers, API keys and cost

Moved out of the README. See also [operations.md](operations.md).

Put keys in `.env` or in real environment variables (these win over `.env`). Never put
them in a committed file. A model is written `provider:model`, and the provider name
decides which key is read.

**What the committed config needs.** Only `ANTHROPIC_API_KEY`. The reviewer runs on
`anthropic:claude-opus-5-5`, the largest model used, and every other agent on
`anthropic:claude-sonnet-5-5`, all at `reasoning_effort: low`. The reviewer's output cap is 3000
tokens: a low-effort review uses a few hundred, and the cap keeps an Opus call's worst-case
budget reservation near 0.07 USD. Opus 5.5 rejects forced tool calls, so Anthropic calls use
native JSON-schema structured output. The reviewer control (`live_proof --live --mode
reviewer`) passed on Opus for 0.06 USD on 2026-10-18.

**A free setup.** Tested on 2026-10-18 and about 3 minutes a week. The thread
writer, marketing writer, research, visual and advisor use `mistralai:ministral-14b-latest`
with `reasoning_effort: none`, because that model rejects the parameter; it answers in about
5 s. The reviewer is `nvidia:nvidia/nemotron-3-super-120b-a12b` with `reasoning_effort: low`,
a different provider and model family from the writers. On 2026-10-18 Ministral failed the
live reviewer control (it rejected the supported text and returned unparseable output for the
invented one), while Nemotron passed it, so the stronger model sits on the gate.
`z-ai/glm-5.3-flash` wrote well but took 36 to 60 s a call on NVIDIA's queue. In that run
(`output/free-run-with-drops`) 3 of 6 shipped: Ministral padded the marketing posts with unsupported
claims, which the reviewer dropped. After the claim-word check, length split and link note were
added, the same models shipped 6 of 6 (`output/free-run-6-of-6`, $0.02). It needs `NVIDIA_API_KEY` and `MISTRALAI_API_KEY`.
`config/prices.yaml` prices the two NVIDIA models at 0 (their hosted endpoint is free) and
Mistral at its paid list price, so every call is costed and no `--allow-unpriced` is needed.
Add other models there.

**A one-key run.** Add `--model-override openrouter:openai/gpt-4.1-mini`. It replaces the
model of every agent, including the three Claude ones. You then need only
`OPENROUTER_API_KEY`.

| Key | Needed when the model starts with |
| --- | --- |
| `OPENROUTER_API_KEY` | `openrouter:` (the default model) |
| `NVIDIA_API_KEY` | `nvidia:` (writers and reviewer in the committed config) |
| `MISTRALAI_API_KEY` | `mistralai:` (research, visual and advisor in the committed config) |
| `GROQ_API_KEY` | `groq:` |
| `GEMINI_API_KEY` | `google_genai:` |
| `OPENAI_API_KEY` | `openai:` |
| `ANTHROPIC_API_KEY` | `anthropic:` (only in the Claude setup) |
| `OPENCODE_API_KEY` | `opencode:` |
| `<PROVIDER>_API_KEY` | any other LangChain provider (its package must be installed) |
| `TAVILY_API_KEY` | optional: web search tries Tavily first. Without it, DDGS is used and needs no key. |
| `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` | optional: both together turn on the Cloudflare FLUX image background. Without them the card is drawn locally with Pillow. |

You only need keys for providers your run uses. If a key is missing, the first call to
that provider fails, that slot is recorded as failed, and the run goes on.

Other settings are optional and listed in [.env.example](../.env.example). A command-line
flag beats the environment, which beats `.env`.

Free tiers change. What we saw on 2026-10-15:

- OpenRouter free models: 50 requests per day.
- Gemini 2.5 Flash free tier: 20 requests per day.
- Groq free tier: 8,000 tokens per minute.
- Mistral free plan: `ministral-14b-latest` at 937,500 tokens per minute and 30 requests
  per minute.
- NVIDIA free key: worked for `nemotron-3-super-120b-a12b`.

A full week needs more calls than the OpenRouter and Gemini free daily limits allow, so
use a paid key or the sample setup below.

## Cost and budget

Defaults are 250,000 tokens and 1 USD for the whole run, including the advisor. Before each
model call the guard reserves the estimated input plus the full output cap. If that does not
fit, the call does not start and the slot is marked `failed_budget`. Finished slots still
export. It is a guard, not a hard cap: estimates can be wrong and providers can bill more.

`--estimate` prints a worst-case ceiling as JSON and exits. It starts no model or search
client, makes no network call and writes no run folder. The ceiling assumes every retry
happens, so it is far above a real run (about 11 million tokens for the committed config,
against 181,069 used by the sample). A model with no known price is refused unless you pass
`--allow-unpriced`; then only the token limit applies. Prices come from `genai-prices`
and the overrides in `config/prices.yaml`. USD covers model calls only. Search
and image calls are counted in `external-usage.json` but not priced.

## The committed sample

The sample in `output/sample-week` came from a live run on 2026-10-18 with the committed
config (Opus 5.5 reviewer, Sonnet 5.5 for every other agent, all at reasoning effort low),
`MODEL_OVERRIDE` empty and the advisor off:

```
python run_week.py --plan plan.json --output <run> --set thread_2="ERC-4626 vault inflation attack" --max-concurrency 3
```

Result: 6 of 6 pieces shipped in 75 seconds, 181,069 tokens, 0.5597 USD confirmed, every call
priced. `thread_2` used Tavily web search (1 call) because its topic is not in the KB; its
first draft was rejected by the code number check (`unsourced_number:10000`) and the retry
shipped. The other five shipped on the first attempt. All six images are Cloudflare FLUX
backgrounds composed into a Pillow card. The full per-slot story is in
`output/sample-week/run.log`.

The free run copied to `output/free-run-with-drops` (Ministral writers, Nemotron reviewer, about 3 minutes)
shows the drop path: 3 of 6 shipped, and the reviewer dropped two marketing posts and one
thread after a retry for claims the evidence does not support. `output/free-run-6-of-6` is the
same setup on the current code: 6 of 6 shipped, three of them after a rejection and a retry.
