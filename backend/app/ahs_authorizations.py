"""AHS authorized a repair: owen-main says so, dispatch hears it (2026-10-01).

owen-main reads the Dispatch mailbox. Among the "American Home Shield sent you a note for job
#<n>" emails, ONE template means AHS approved the repair:

    Note Added in Frontdoor System NCC $#### Net Total $#### AUTHO # ####RNCL Thanks for
    being the best!

owen-main recognises it (`providers/dispatch_email.py` over there) and posts it here as
`kind: "authorization"` with the AHS job number, the AUTHO number and the amounts. A note
saying "Your list of items to service have been updated" MIGHT be an approval and arrives as
`kind: "authorization_possible"`. Only ONE real authorization email had ever been seen when
this was built, so it is OFF on owen-main (`CRM_LINK_AHS_AUTHORIZATIONS_ENABLED=false`) and
must be watched when it is switched on.

What it does here — NO new table, NO migration, NOTHING sent to Zuper, nothing queued:

* **authorization** -> a `DispatchEvent` keyed `ahs_auth:<job>:<autho>` and the bell through
  `dispatch.alerts.fire` (kind `ahs_approved`, "AHS approved #<n>: $<net>") for the Dispatch
  audience, plus a `DispatchItem` (same key) in the "Book a visit" queue telling the office to
  move the job to AHS Approved in Zuper, answer its questions and book the repair.
* **authorization_possible** -> the item only (kind `ahs_items_updated`), no bell:
  "AHS may have updated #<n> — check the portal".
* **Idempotent.** The same key twice answers 200 `outcome: existing` and writes nothing, so a
  repeat email or a retry is one bell and one item.
* **The job is looked up, never required.** By the CRM card with `custom_fields.ahs_job_id`
  (an AHS email card), then the Zuper job that card mirrors, else a Dispatch job carrying the
  number. Not found still rings and keeps the item with the AHS number in it. A card or board
  hidden from the token's owner is treated as not found (`pipeline_access`), as `ahs_jobs` does.
* **A Dispatch pass never resolves these items** (`dispatch.rules.FED_KINDS`): the rules did
  not make them, so the rules not finding them means nothing. Done / Wrong close them.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ahs_jobs, auth, pipeline_access
from .db import get_db
from .dispatch import alerts, rules
from .dispatch import config as dc
from .models import (
    DispatchEvent,
    DispatchItem,
    DispatchJob,
    Job,
    Opportunity,
    Pipeline,
    ZuperMapping,
)
from .zuper import mapping

log = logging.getLogger("ahs_authorizations")
router = APIRouter(tags=["ahs"])

PATH = "/api/ahs-jobs/authorizations"
APPROVED = "ahs_approved"
ITEMS_UPDATED = "ahs_items_updated"
QUEUE = "book"
TODO_APPROVED = ("Move the job to AHS Approved, answer its questions (AHS authorized $), "
                 "then book the repair")
TODO_UPDATED = ("Open the job in the AHS portal and see what changed. If AHS approved the "
                "repair, move the job to AHS Approved and book it")
KEY_MAX = 200

_AUTHO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,39}$")
_CODE = re.compile(r"^[A-Za-z]{1,12}$")


def _money(v):
    """'1,350.00' / '$1350' -> '1350.00' / '1350'. A string, never a float; refused when it is
    not a non-negative number, because a garbled amount would put a wrong figure in the bell."""
    if v is None:
        return None
    raw = str(v).replace("$", "").replace(",", "").strip()
    if not raw:
        return None
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise ValueError("amounts must be numbers") from None
    if not d.is_finite() or d < 0:
        raise ValueError("amounts must be numbers of zero or more")
    return raw


class AhsAuthorizationIn(BaseModel):
    """owen-main's `email_jobs.authorization_body`."""
    kind: Literal["authorization", "authorization_possible"]
    ahs_job_id: str
    autho_number: str | None = Field(None, max_length=40)
    autho_code: str | None = Field(None, max_length=12)
    net_total: str | None = Field(None, max_length=20)
    ncc: str | None = Field(None, max_length=20)
    dedupe_key: str | None = Field(None, max_length=600)
    message_id: str | None = Field(None, max_length=500)
    received_at: datetime | None = None

    model_config = {"extra": "ignore"}

    _id = field_validator("ahs_job_id", mode="after")(ahs_jobs._job_id)

    @field_validator("net_total", "ncc", mode="before")
    @classmethod
    def _amounts(cls, v):
        return _money(v)

    @field_validator("autho_number", "autho_code", "message_id", "dedupe_key", mode="after")
    @classmethod
    def _blank(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _shape(self):
        if self.autho_number and not _AUTHO.match(self.autho_number):
            raise ValueError("autho_number must be letters, digits or dashes")
        if self.autho_code and not _CODE.match(self.autho_code):
            raise ValueError("autho_code must be letters")
        if self.kind == "authorization" and not self.autho_number:
            raise ValueError("an authorization needs its autho_number")
        return self


# ---------------------------------------------------------------- pieces

def key_for(body: AhsAuthorizationIn) -> str:
    """Per (job, AUTHO) for an approval; per email for "items updated", which has no number."""
    if body.kind == "authorization":
        return ("ahs_auth:%s:%s" % (body.ahs_job_id, body.autho_number))[:KEY_MAX]
    which = body.message_id or body.dedupe_key or (
        body.received_at.isoformat() if body.received_at else "")
    return ("ahs_auth_possible:%s:%s" % (body.ahs_job_id, which))[:KEY_MAX]


def dollars(amount: str | None) -> str | None:
    """'1350' -> '$1,350'; '1350.5' -> '$1,350.50'."""
    if amount is None:
        return None
    d = Decimal(amount)
    return f"${d:,.2f}" if d != d.to_integral_value() else f"${int(d):,}"


def _dispatch_job_for(db: Session, job_id: str, card: Opportunity | None) -> DispatchJob | None:
    """The Zuper job (as the Dispatch page last read it) for this AHS number: the one the card
    mirrors, else one whose number or an AHS field holds the number."""
    if card is not None:
        uid = db.scalar(select(ZuperMapping.zuper_uid).where(
            ZuperMapping.crm_type == "opportunity", ZuperMapping.crm_id == card.id,
            ZuperMapping.zuper_type == "job"))
        if uid:
            j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == uid))
            if j is not None:
                return j
    j = db.scalars(select(DispatchJob).where(DispatchJob.job_number == job_id)
                   .order_by(DispatchJob.id)).first()
    if j is not None:
        return j
    for j in db.scalars(select(DispatchJob).where(DispatchJob.fields.is_not(None))
                        .order_by(DispatchJob.id)):
        for label, value in (j.fields or {}).items():
            if "ahs" in str(label).lower() and str(value or "").strip() == job_id:
                return j
    return None


def locate(db: Session, job_id: str, principal: auth.Principal) -> dict:
    """{card, job, board} for this AHS number — each None when not found or hidden from the
    token's owner. Never raises: a lookup that fails is a job not found, and the bell still
    rings."""
    found: dict = {"card": None, "job": None, "board": None}
    try:
        card = ahs_jobs.card_for(db, job_id)
        if card is not None and not pipeline_access.can_see(db, principal, card.pipeline_id):
            card = None
        job = _dispatch_job_for(db, job_id, card)
        hidden = alerts.hidden_boards(db, principal.user_id, principal.role)
        if job is not None and job.board and job.board in hidden:
            job = None
        if job is not None and card is None:
            opp_id, _ = alerts.card_for(db, job.job_uid)
            o = db.get(Opportunity, opp_id) if opp_id else None
            if o is not None and pipeline_access.can_see(db, principal, o.pipeline_id):
                card = o
        board = job.board if job is not None else None
        if board is None and card is not None:
            name = db.scalar(select(Pipeline.name).where(Pipeline.id == card.pipeline_id))
            board = mapping.mirrored_categories().get(name, name) if name else None
        found.update(card=card, job=job, board=board)
    except Exception:
        log.exception("AHS authorization: looking up job %s failed", job_id)
    return found


def _texts(body: AhsAuthorizationIn, found: dict) -> tuple[str, str, str]:
    """(title, why, todo)."""
    job, card = found["job"], found["card"]
    who = (job.customer_name if job is not None else None) or None
    when = (body.received_at or datetime.now(UTC))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    when = dc.local(when)
    day = "%s %d" % (when.strftime("%b"), when.day)
    where = ""
    if job is None and card is None:
        where = (" Nothing in the CRM or on the Dispatch page carries AHS job #%s yet — find "
                 "it in Zuper by the AHS number." % body.ahs_job_id)
    if body.kind == "authorization":
        net, ncc = dollars(body.net_total), dollars(body.ncc)
        title = "AHS approved #%s" % body.ahs_job_id + (": %s" % net if net else "")
        if who:
            title += " — %s" % who
        autho = body.autho_number + (body.autho_code or "")
        facts = ["AUTHO #%s" % autho]
        if net:
            facts.append("net total %s" % net)
        if ncc:
            facts.append("NCC %s" % ncc)
        why = "AHS sent an authorization note on %s: %s.%s" % (day, ", ".join(facts), where)
        todo = TODO_APPROVED
        if net or ncc:
            todo = todo.replace("AHS authorized $", "AHS authorized " + (net or ncc))
        return title[:300], why, todo + "."
    title = "AHS may have updated #%s — check the portal" % body.ahs_job_id
    if who:
        title += " (%s)" % who
    why = ("AHS sent a note on %s: \"Your list of items to service have been updated\". It may "
           "mean the repair was approved; it may not.%s" % (day, where))
    return title[:300], why, TODO_UPDATED + "."


def _jobs_count(db: Session) -> int:
    return db.scalar(select(func.count(Job.id))) or 0


# ---------------------------------------------------------------- the write

def deliver(db: Session, body: AhsAuthorizationIn,
            principal: auth.Principal) -> tuple[int, dict]:
    """(status code, payload). Flushes; the route commits."""
    ahs_jobs._lock(db, body.ahs_job_id)
    key = key_for(body)
    kind = APPROVED if body.kind == "authorization" else ITEMS_UPDATED
    existing = db.scalar(select(DispatchItem).where(DispatchItem.key == key))
    rang = db.scalar(select(DispatchEvent.id).where(DispatchEvent.key == key))
    if existing is not None or rang is not None:
        return 200, {"outcome": "existing", "ahs_job_id": body.ahs_job_id, "kind": body.kind,
                     "key": key, "item_id": existing.id if existing else None, "alerts": 0}

    jobs_before = _jobs_count(db)
    now = datetime.now(UTC)
    found = locate(db, body.ahs_job_id, principal)
    job, card = found["job"], found["card"]
    title, why, todo = _texts(body, found)
    received = body.received_at or now
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    item = DispatchItem(
        key=key, kind=kind, queue=QUEUE, state="open", created_at=now, updated_at=now,
        job_uid=job.job_uid if job is not None else None,
        job_number=(job.job_number if job is not None and job.job_number else body.ahs_job_id),
        board=found["board"],
        phone=(job.phones or [None])[0] if job is not None else None,
        title=title, why=why, todo=todo, urgent=False,
        due_at=dc.add_business_minutes(received, dc.SLA_MINUTES[QUEUE]),
        evidence={"ahs_job_id": body.ahs_job_id, "autho_number": body.autho_number,
                  "autho_code": body.autho_code, "net_total": body.net_total,
                  "ncc": body.ncc, "message_id": body.message_id,
                  "received_at": received.isoformat(), "source": "AHS note email",
                  "opportunity_id": card.id if card is not None else None})
    db.add(item)
    db.flush()

    written = 0
    if body.kind == "authorization":
        event = rules.Event(
            key=key, kind=APPROVED, title=title, body=why,
            job_uid=job.job_uid if job is not None else None,
            opportunity_id=card.id if card is not None else None,
            contact_id=card.contact_id if card is not None else None)
        boards = {(event.job_uid or ""): found["board"]} if found["board"] else {}
        written = alerts.fire(db, [event], ring=True, boards=boards)
        db.flush()

    if _jobs_count(db) != jobs_before:
        # Nothing here may queue a text or anything else; the route never commits this.
        raise RuntimeError("an AHS authorization enqueued a job; refusing to commit")
    return 201, {"outcome": "created", "ahs_job_id": body.ahs_job_id, "kind": body.kind,
                 "key": key, "item_id": item.id, "alerts": written,
                 "found": job is not None or card is not None,
                 "opportunity_id": card.id if card is not None else None,
                 "job_uid": job.job_uid if job is not None else None}


@router.post(PATH, status_code=201)
def ingest_ahs_authorization(body: AhsAuthorizationIn, response: Response,
                             db: Session = Depends(get_db),
                             principal: auth.Principal = auth.EVENTS_INGEST):
    """AHS approved (or may have changed) a job's repair: a Dispatch item, and for an approval
    the bell. A repeat answers 200 `outcome: existing` and writes nothing. Writes nothing to
    Zuper and queues nothing."""
    code, out = deliver(db, body, principal)
    if code == 201:
        db.commit()
    response.status_code = code
    return out
