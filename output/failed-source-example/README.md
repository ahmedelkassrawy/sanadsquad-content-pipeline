# Failed source example (2026-10-18)

A live single-piece run on the free setup (Ministral research, Nemotron reviewer) for a topic
that does not exist:

```
python run_piece.py --type tech-thread --topic "Zorvex ledger re-pricing exploit in Cairo 9 vaults"
```

The brief says that when a source fails, the slot is marked failed rather than written from
invented facts. `run.log` shows that path: research searched the knowledge base three times and
the web once, found nothing that supports the topic, and the slot ended as `failed_research`.
No draft was written and nothing was exported. It cost $0.0069 in LLM calls.

`research_log.json` holds every search and the evidence each one returned. Only these readable
files were copied, so this folder cannot be resumed.
