"""Recognising a carrier delivery receipt that was filed as a customer's text (2026-09-16).

## What went wrong

BulkVS posts delivery receipts to the SAME webhook it posts inbound texts to. owen-main
stored each one as an inbound message and relayed it here like any other, so this CRM put
them on a customer's thread as words the customer had written:

    id:1162999967 sub:001 dlvrd:000 submit date:2609160247 done date:2609160247
    stat:UNDELIV err:255 text:Dream Te...

Measured on production: one number-only thread holding two of these. They are not messages.
They are the answer to "did the text arrive?" — the exact question the owner asked for — and
they were being shown as the question instead.

The fix is owen-main's, and it is made there: receipts are recognised at the webhook and
never relayed. This module is the CRM's own half, for two things owen-main cannot do:

  * **a guard on ingest.** `POST /api/events` refuses to file a receipt as a message even
    if it is sent one. That matters because of DEPLOY ORDER: this CRM and owen-main are
    deployed separately, and for however long an older owen-main is running, every receipt
    it relays would otherwise land in a customer's conversation. The guard costs one regex
    per inbound text and makes the CRM safe on its own;
  * **cleaning up what already landed** — `app/dlr_cleanup.py`.

## The shape, and why the match is strict

The SMPP `deliver_sm` receipt format: seven fields, in order, anchored at the start. `text:`
is optional because it is a truncated echo a carrier may omit. Kept deliberately narrow —
the property that matters is that a REAL CUSTOMER TEXT IS NEVER MISTAKEN FOR ONE, and a
person cannot type this by accident. A carrier variant this does not recognise is the old
behaviour, which is no worse than today and is logged loudly on the owen-main side.

This pattern is a second copy of `providers/bulkvs._DLR` in owen-main, and that is
deliberate rather than sloppy: the two repositories deploy independently, and a guard that
depended on the other side having been deployed first would not be a guard.
"""
import re

# id:<id> sub:<n> dlvrd:<n> submit date:<ts> done date:<ts> stat:<WORD> err:<code> [text:...]
_RECEIPT = re.compile(
    r"^\s*id:\S+"
    r"\s+sub:\d+"
    r"\s+dlvrd:\d+"
    r"\s+submit\s+date:\d{8,14}"
    r"\s+done\s+date:\d{8,14}"
    r"\s+stat:[A-Za-z]+"
    r"\s+err:\w+"
    r"(?:\s+text:.*)?$",
    re.DOTALL,
)

_FIELDS = re.compile(
    r"^\s*id:(?P<id>\S+)"
    r"\s+sub:\d+"
    r"\s+dlvrd:\d+"
    r"\s+submit\s+date:(?P<submit>\d{8,14})"
    r"\s+done\s+date:\d{8,14}"
    r"\s+stat:(?P<stat>[A-Za-z]+)"
    r"\s+err:(?P<err>\w+)"
    r"(?:\s+text:(?P<text>.*))?$",
    re.DOTALL,
)

REFUSED = ("ignored: a carrier delivery receipt, not a message — "
           "see app/dlr.py and DECISIONS.md, 2026-09-16")


def looks_like_receipt(body: str | None) -> bool:
    """True when this message body is a carrier delivery receipt.

    The only question asked of it, and the only one it should ever answer. Anything that is
    not plainly one of these is a customer's words.
    """
    return bool(body) and _RECEIPT.match(body) is not None


def fields(body: str | None) -> dict | None:
    """The receipt's id, submit time, stat and err — for the cleanup report, so a person can
    see WHAT was hidden without the report quoting a customer's thread at them."""
    if not body:
        return None
    m = _FIELDS.match(body)
    if m is None:
        return None
    return {"id": m.group("id"), "submit": m.group("submit"),
            "stat": m.group("stat").upper(), "err": m.group("err"),
            "text": (m.group("text") or "").strip()}
