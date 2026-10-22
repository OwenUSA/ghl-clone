"""Agent #9, "Reviews & QC" (2026-10-22). Read this first.

    config.py    gates, settings (AI Agents -> Overview -> Reviews & QC)
    rules.py     who is asked, the exact words, reading a 1-5 reply (PURE)
    zconnect.py  its Zuper reads, and its two writes (text, private note) in client.reviews_write()
    service.py   the pass: plan, ask, read replies, no reply -> Anisa (off)

A finished job (Repair Complete, 2 days later) whose customer said nothing unhappy is asked how we
did, 1-5. Every customer who answers is offered the Google review link (never only the happy
ones - Google's rule); 1-4 or a leak also asks what to improve and alerts the office ("Customer
issues" + the bell). Off unless REVIEWS_ENABLED; texts need REVIEWS_TEXTS_ENABLED AND the
registry's "Text customers" above Shadow; calls are at Never. Never moves a Zuper column.
"""
