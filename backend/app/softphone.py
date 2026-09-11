"""The CRM end of the browser softphone: credentials, and who is calling.

The owner's requirement is one sentence — *"i should be able to answer from the crm
or the other 2 phones, the one that picks up first, takes the call"* — and most of
it already worked. `+19544829099` is bound, and OWEN's hybrid ring group already
rings the two mobiles **and** every `PJSIP/operator-<slug>` browser softphone at
once, bridging whichever answers first and hanging up the rest. The CRM was simply
not one of those softphones.

Two endpoints make it one:

* **`POST /api/softphone/credentials`** — hands this browser the short-lived SIP +
  TURN credentials it needs to register as its user's OWEN operator endpoint.
* **`GET /api/softphone/caller`** — turns the ringing number into a name.

## The API key never reaches the browser

OWEN's minting route (`POST /api/crm-link/softphone/credentials`) is authenticated
with the CRM-link machine key. That key can also place calls and send texts on a
bound DID, so it is a credential that must never leave a server. This module holds
it, the browser holds a session cookie, and the trust boundary is this process:

    browser ──cookie──▶ /api/softphone/credentials ──X-OWEN-Key──▶ OWEN

The reply *does* carry a SIP password down to the browser, and that is unavoidable:
a WebRTC softphone registers from the browser, so the browser must know the digest
password. That is why the credentials are short-lived and why OWEN caps their TTL
below its own — see `CRM_LINK_SOFTPHONE_TTL_SECONDS` there.

## Off unless configured

With `OWEN_BASE_URL` or `OWEN_SOFTPHONE_KEY` unset this answers **503** and the UI
shows "softphone not configured" rather than a spinner. That is the default in
tests, in a fresh checkout and in any deployment that has not been told about the
phone system, so nothing here can reach a live telephony service by accident.

## Nothing minted is logged

A credential blob is never written to a log line, an exception message, or an error
body returned to the browser. Upstream refusals are relayed verbatim because they
are written for a machine caller and say exactly what is missing; upstream
*credentials* are relayed to the browser and to nowhere else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, phone_match
from .db import get_db
from .models import Contact

router = APIRouter(prefix="/api/softphone", tags=["softphone"])

# The path OWEN publishes. Named here once so a rename upstream is one edit.
CREDENTIALS_PATH = "/api/crm-link/softphone/credentials"

# A caller-ID shorter than a full national number is not an identification. "Anonymous",
# a blocked-caller placeholder and a two-digit test extension must never be matched
# against the contact book — `phone_match` would happily match a fragment *anywhere* in a
# stored number, and putting a stranger's name on a ringing call is worse than showing
# digits. Ten digits or nothing.
IDENTIFIABLE_DIGITS = phone_match.NATIONAL_DIGITS

NOT_CONFIGURED = ("the softphone is not configured on this deployment "
                  "(set OWEN_BASE_URL and OWEN_SOFTPHONE_KEY)")
UNREACHABLE = "the phone system did not answer — your browser cannot take calls right now"


@dataclass(frozen=True)
class OwenConfig:
    """Where OWEN is and how we authenticate to it. Read per request rather than at
    import, so a deployment can be reconfigured with a restart and a test can set the
    environment after this module is imported."""

    base_url: str = ""
    key: str = ""
    timeout: float = 5.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.key)


def config() -> OwenConfig:
    """The live configuration. Defensive about a junk timeout: a deployment typo must not
    make credential minting hang a request thread for however long the default socket
    timeout happens to be."""
    try:
        timeout = float(os.getenv("OWEN_SOFTPHONE_TIMEOUT", "5") or 5)
    except ValueError:
        timeout = 5.0
    return OwenConfig(
        base_url=os.getenv("OWEN_BASE_URL", "").rstrip("/"),
        key=os.getenv("OWEN_SOFTPHONE_KEY", ""),
        timeout=max(1.0, min(timeout, 30.0)),
    )


def relay_status(upstream: int) -> int:
    """The status WE answer with, given OWEN's.

    Relayed rather than flattened, because these mean different things to the person
    holding the browser and the UI says something different for each:

      * **503** — the link is off, or telephony is dark. "Not available right now."
      * **403** — this user is not a provisioned operator. "Ask for a softphone."
      * **422** — we sent something malformed. Our bug, and it should be visible.
      * anything else — OWEN is broken or unreachable: **502**, never a 200 with an
        empty blob, because a softphone that silently fails to register is the exact
        failure this feature exists to make visible.
    """
    if upstream in (403, 422, 503):
        return upstream
    return 502


def post_json(url: str, *, key: str, payload: dict, timeout: float) -> tuple[int, object]:
    """One HTTP round trip to OWEN. Isolated so tests replace it wholesale — a test must
    never be able to reach a live telephony service, not even by misconfiguration.

    Returns `(status, parsed body)`; a body that is not JSON comes back as text, which
    is what a proxy's HTML error page looks like.
    """
    with httpx.Client(timeout=timeout) as client:
        # X-OWEN-Key rather than Authorization: OWEN accepts both, and keeping the CRM's
        # own bearer scheme distinct from OWEN's makes a mis-sent credential obvious.
        resp = client.post(url, json=payload, headers={"X-OWEN-Key": key})
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text


def _detail(body: object, fallback: str) -> str:
    """The human-readable reason out of an upstream error body, or `fallback`.

    OWEN's refusals are written for a machine caller that cannot ask a follow-up
    question ("that CRM user is not a provisioned OWEN operator..."), so they are far
    better than anything this end could invent. A body that carries nothing useful is
    replaced rather than echoed — an HTML error page in a dialog helps nobody.
    """
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail
    return fallback


@router.post("/credentials")
def softphone_credentials(principal: auth.Principal = auth.ANY_USER) -> dict:
    """Short-lived SIP + TURN credentials for THIS user's browser.

    The user's own email is the identity — never a parameter. A request body naming
    someone else's email would let any signed-in user register as any operator, and
    since an operator endpoint is a ring destination, that is "answer other people's
    calls" rather than a privilege escalation on paper only.

    Every role may hold a softphone. A TECH can already send a message and move an
    opportunity; answering the phone is the same kind of act, and a ring group that
    excluded the field crew would miss the call it exists to catch.
    """
    cfg = config()
    if not cfg.configured:
        raise HTTPException(503, NOT_CONFIGURED)

    try:
        status, body = post_json(
            cfg.base_url + CREDENTIALS_PATH,
            key=cfg.key,
            payload={"email": principal.email},
            timeout=cfg.timeout,
        )
    except httpx.HTTPError:
        # Deliberately not `except Exception`: a bug in this function should surface as a
        # 500 with a traceback, not be relabelled as "the phone system is down".
        raise HTTPException(502, UNREACHABLE) from None

    if status != 200:
        raise HTTPException(relay_status(status), _detail(body, UNREACHABLE))
    # A 200 carrying something that is not a credential blob is a broken upstream, and
    # handing it to SIP.js would fail somewhere far less legible. Checked on the fields the
    # browser actually registers with rather than on the key being PRESENT: `{"sip": null}`
    # has a `sip` key and is still not a credential.
    sip = body.get("sip") if isinstance(body, dict) else None
    if not isinstance(sip, dict) or not sip.get("wss_url") or not sip.get("username"):
        raise HTTPException(502, UNREACHABLE)
    return body


def find_contact(db: Session, number: str) -> Contact | None:
    """The contact whose number is `number`, matched on the last ten digits.

    The same identity rule as everywhere else in this app and as OWEN's — see
    `phone_match`. Anything shorter than a full national number identifies nobody
    (`IDENTIFIABLE_DIGITS`).

    Ambiguity resolves to the lowest id rather than to nothing: two contacts sharing a
    phone number is a real thing in a household, and showing one of the two names beats
    showing bare digits on a phone that is ringing *now*. The duplicate is a data problem
    to fix in Contacts, not something to litigate while someone waits for an answer.
    """
    if len(phone_match.digits(number)) < IDENTIFIABLE_DIGITS:
        return None
    return db.execute(
        select(Contact)
        .where(phone_match.phone_clause(Contact.phone, number))
        .order_by(Contact.id)
        .limit(1)
    ).scalars().first()


@router.get("/caller")
def softphone_caller(number: str = Query(default="", max_length=40),
                     db: Session = Depends(get_db),
                     principal: auth.Principal = auth.ANY_USER) -> dict:
    """Who is calling, for the incoming-call card.

    Best-effort by contract: an unknown number is a 200 with `contact: null`, never a
    404. The UI falls back to the formatted digits, and a lookup that failed must never
    be able to stop somebody answering the phone.
    """
    contact = find_contact(db, number)
    return {
        "number": number,
        "contact": None if contact is None else {
            "id": contact.id,
            "name": contact.name or None,
            "phone": contact.phone,
            "business_name": contact.business_name,
        },
    }
