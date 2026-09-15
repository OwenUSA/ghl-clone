"""AI Agents, phase 1 — the foundation (DECISIONS.md, 2026-09-15).

    vault.py       Fernet encryption of provider API keys under AI_SECRETS_KEY
    providers.py   ONE provider interface; Anthropic and OpenAI / OpenAI-compatible adapters
    pricing.py     known model prices and the cost of a run
    prompt.py      the deterministic system prompt compiled from an agent's parts
    knowledge.py   knowledge bases: text extraction, chunks, the lexical retriever, gaps
    actions.py     the action catalogue: tool schemas, validation, what each write does
    engine.py      one run: skip rules, the tool loop, Off / Suggest / Auto-pilot / Try-it
    triggers.py    what the rest of the CRM calls when something happens
    config.py      an agent's config: defaults, validation, the catalogue of triggers
    api.py         the /api/ai routes

Nothing in this package runs by itself: every agent is created Off, a global pause is checked
before every run, and a number no contact holds never triggers anything.
"""
