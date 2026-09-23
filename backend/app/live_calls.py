"""The CRM end of a live AI-agent call: what is live, Listen, and Take over (2026-09-23).

The owner's requirement behind the whole take-over design, from owen-main's spec: *"I want a
person to listen to the call and be able to take control if the agent is not working
properly."* owen-main has done both since the agent build, from its OWN UI. The CRM is where
the office works, so the banner is here (DECISIONS, 2026-09-22, Q13), and this module is the
relay — the same shape as `app/openphone.py` and `app/softphone.py`:

    browser ──cookie──▶ /api/live-calls ──X-OWEN-Key──▶ owen-main /api/crm-link/live-calls

## Who may

ADMIN and DISPATCHER, never a TECH and never a user with "Only assigned data" on — the AI
module's rule, reused (`ai.api.VIEW`) rather than written a second time, because these are
AI calls and the two must not disagree about who supervises them. The browser draws the
banner by the same rule (`lib/aiAgents.ts` `canOpenAiAgents`).

owen-main does not trust that: listen and takeover resolve the email we send through its
operator roster and refuse an unprovisioned one by name. **The email is always the signed-in
user's own.** There is no body, so nobody can ring a colleague — or a stranger's line — in
to a customer's call.

## Off unless configured

With `CRM_LINK_BASE_URL` or `CRM_LINK_API_KEY` unset, the list is `{"calls": []}` and no
request is made: the banner draws nothing, which is the truth — nothing we can see is live.
Listen and Take over answer 503 without a request. The same default as every other path
into owen-main, and the default in tests and in a fresh checkout.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from . import assigned_access, auth, crmlink, softphone
from .ai.api import VIEW as AI_VIEW
from .db import get_db

log = logging.getLogger("live_calls")

router = APIRouter(prefix="/api/live-calls", tags=["live-calls"])

NOT_CONFIGURED = "the phone link is not configured"
ENDED = "That call has already ended."

# An Asterisk linkedid is `<epoch>.<n>`; owen-voice passes it through. It goes into a URL on
# a machine key, so anything else — a dot-dot, a slash smuggled in encoded, a 2 KB string —
# is refused here as "that call has ended" without a request.
_LINKEDID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _names(db: Session, principal: auth.Principal, calls: list[dict]) -> list[dict]:
    """Each call with the CONTACT who rang, when the CRM knows them — "AI is on a call with
    Jane Doe" rather than digits. The same ten-digit rule as the incoming-call card
    (`softphone.find_contact`), and asked through `assigned_access.scope` like that card
    does, although a restricted user never gets this far: if the gate above ever widened,
    a customer outside the reader's jobs would still show as a bare number."""
    scope = assigned_access.scope(db, principal)
    out = []
    for c in calls:
        if not isinstance(c, dict) or not c.get("linkedid"):
            continue
        contact = softphone.find_contact(db, str(c.get("caller_number") or ""))
        if contact is not None and not scope.sees_contact(contact.id):
            contact = None
        out.append({
            "linkedid": str(c["linkedid"]),
            "caller_number": c.get("caller_number"),
            "dialed_number": c.get("dialed_number"),
            "agent": c.get("agent"),
            "started_at": c.get("started_at"),
            "duration_s": c.get("duration_s"),
            "turns": c.get("turns"),
            "contact": None if contact is None else {
                "id": contact.id, "name": contact.name or None},
        })
    return out


@router.get("")
def list_live_calls(db: Session = Depends(get_db),
                    principal: auth.Principal = AI_VIEW) -> dict:
    """The AI-agent calls in progress. Polled every 5s by the banner.

    Never an error for the browser: an unconfigured link or an owen-main that did not
    answer is `{"calls": []}`. The status dot already says the link is down, and a banner
    that turned into an error box every five seconds would be a second, louder copy of it.
    `unavailable` carries the sentence for anyone reading the payload by hand."""
    if not crmlink.configured():
        return {"calls": []}
    result = crmlink.live_calls()
    if not result.ok:
        log.info("live calls unavailable (%d): %s", result.status, result.reason)
        return {"calls": [], "unavailable": result.reason}
    return {"calls": _names(db, principal, list((result.data or {}).get("calls") or []))}


def _relay(linkedid: str, action: str, principal: auth.Principal) -> dict:
    if not _LINKEDID.match(linkedid or ""):
        raise HTTPException(404, ENDED)
    if not crmlink.configured():
        raise HTTPException(503, NOT_CONFIGURED)
    # Logged BEFORE the request: a takeover seizes a customer's call, and "who did that"
    # must be answerable even if owen-main's reply never arrives.
    log.info("live call %s: %s requested by %s", linkedid, action, principal.email)
    result = crmlink.live_call_action(linkedid, action, principal.email)
    if result.ok:
        data = result.data or {}
        return {"ok": True, "operator": data.get("operator"),
                "operator_channel": data.get("operator_channel")}
    # owen-main's own sentence, already turned into the owner's words by crmlink._human.
    # 403 (not a provisioned operator), 404 (the call ended) and 503 (link or telephony
    # off) mean different things on screen, so they are passed through; anything else is
    # owen-main unreachable or broken.
    status = result.status if result.status in (403, 404, 503) else 502
    raise HTTPException(status, result.reason or "could not reach the phone system")


@router.post("/{linkedid}/listen")
def listen_to_live_call(linkedid: str, principal: auth.Principal = AI_VIEW) -> dict:
    """Ring the signed-in user's browser line and let them hear the call. Nobody on the call
    hears them."""
    return _relay(linkedid, "listen", principal)


@router.post("/{linkedid}/takeover")
def take_over_live_call(linkedid: str, principal: auth.Principal = AI_VIEW) -> dict:
    """Stop the agent and put the signed-in user on the call with the customer. It cannot be
    handed back to the agent — the browser confirms before it sends this."""
    return _relay(linkedid, "takeover", principal)
