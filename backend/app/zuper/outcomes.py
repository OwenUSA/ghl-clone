"""Where an owner-approved rule on Zuper money documents WOULD hook in. None ship; it is off.

The owner's decision (2026-09-16, v2): NO automations run on Zuper jobs in the CRM. "Invoice
fully paid -> card Won" and "quote declined -> urgent task" were built and then DROPPED. The
CRM shows quotes, invoices and payments read-only (the money panels) and follows the job's
value, and that is all.

If the owner later approves a rule, it is ONE function registered in `RULES` and `ENABLED` set
True in a reviewed change, with a DECISIONS.md amendment. A rule receives the sync context, the
cached document (`ZuperDocument`: kind, number, status, total, balance, dates) and the card; it
writes through `ctx.db` (a quiet session: it queues nothing back to Zuper) and must be
idempotent, because the same document is delivered again by webhooks and by the sweep.
`tests/test_zuper_sync.py` pins that nothing runs today.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ENABLED = False
RULES: list[Callable[[Any, Any, Any], None]] = []


def document_changed(ctx, doc, opportunity) -> int:
    """Called for every quote or invoice stored for a sent card. Returns how many rules ran."""
    if not ENABLED:
        return 0
    for rule in RULES:
        rule(ctx, doc, opportunity)
    return len(RULES)
