"""American Home Shield work orders, delivered by owen-main, become cards (2026-09-14).

owen-main reads the Dispatch mailbox (`notifications@dispatch.me`), parses each AHS
work order and has relayed it to GoHighLevel since before this CRM existed. It now
ALSO posts the same parsed order here, as its own queued job, so the owner's
decisions (DECISIONS.md, 2026-09-14) are implemented on this side as:

* **The card.** Pipeline "Dream Team Roofing AHS", stage "New Lead", status open,
  titled "<job id> <service> - <customer>" exactly as GoHighLevel's relay names it,
  valued in integer cents, source "AHS", `created_by = "AHS email"`.
* **A new card per AHS job**, repeat customer or not. GoHighLevel's one-card-per-
  contact limit is not a rule here.
* **The customer** is matched by phone (last ten digits, `phone_match`), then by
  email, and otherwise CREATED from the email. A work order from AHS is not the
  spam caller the no-auto-contact rule (2026-09-13) protects against.
* **`custom_fields.ahs_job_id`** is the idempotency key and the reserved,
  read-only, never-drawn namespace `ahs_job_*` (`custom_fields.RESERVED_PREFIXES`).
  The same email delivered twice answers with the card that exists.
* **The full work order** goes on the card as an opportunity NOTE, which is
  STAFF-only on every path already.
* **A cancellation** notes the card for that job and leaves it open. No card is a
  recorded no-op, not an error.

**Nothing is notified.** Like `workiz_import`, this module does not import
`app.automations` or `app.queue`, and `deliver_job` counts the `jobs` table before
and after inside the transaction: one new row is a 500 and the whole write rolls
back. The number-thread adoption listener still runs when a contact is created
(that is the point of it), and it enqueues nothing either.

THE CONTRACT with the Workiz importer: a card made here has `created_by ==
"AHS email"`, `custom_fields.ahs_job_id` set and NO `custom_fields.workiz_id`. The
importer finds an email card by exactly those three facts. `test_ahs_jobs.py` pins
them; change none of them without changing the importer in the same commit.
"""
import re
import zlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictInt, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import auth, custom_fields, phone_match, pipeline_access
from .models import Contact, Job, Opportunity, OpportunityNote, Pipeline, Stage
from .phones import store_phone

PIPELINE_NAME = "Dream Team Roofing AHS"
STAGE_NAME = "New Lead"
SOURCE = "AHS"
CREATED_BY = "AHS email"
AHS_JOB_ID = custom_fields.AHS_JOB_ID
# The zone a cancellation note's date is written in: the account's, the same one
# every appointment is picked in.
ACCOUNT_TZ = ZoneInfo("America/New_York")

TITLE_MAX = 120                     # main.OPPORTUNITY_TITLE_MAX
NOTE_MAX = 20000                    # opportunity_workspace.NOTE_MAX
CANCELLED_PREFIX = "JOB CANCELLED by AHS: job "
AUTOMATION = "none: an AHS work order notifies nobody"

_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,39}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# "..., FL 33186" or "..., FL 33186-1234" at the very end of the address.
_STATE_ZIP = re.compile(r",\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def _job_id(v: str) -> str:
    v = (v or "").strip()
    if not _JOB_ID.match(v):
        raise ValueError("ahs_job_id must be 1-40 letters, digits or dashes")
    return v


class AhsJobIn(BaseModel):
    """One parsed AHS work order. owen-main builds it from `inbound_emails.fields`."""
    ahs_job_id: str
    customer_name: str = Field(min_length=1, max_length=240)
    service: str | None = Field(None, max_length=80)
    phone: str | None = Field(None, max_length=40)
    email: str | None = Field(None, max_length=255)
    service_address: str | None = Field(None, max_length=400)
    # Integer cents, converted from the email's decimal total on the far side. A
    # float is refused rather than rounded: the brief's "no float drift" is a rule
    # about the wire, not only about the column.
    value_cents: StrictInt = Field(0, ge=0)
    # The work order as GoHighLevel's note gets it (`job_description`).
    description: str | None = Field(None, max_length=NOTE_MAX)
    message_id: str | None = Field(None, max_length=500)
    received_at: datetime | None = None

    model_config = {"extra": "ignore"}

    _id = field_validator("ahs_job_id", mode="after")(_job_id)

    @field_validator("customer_name", "service", "phone", "email", "service_address",
                     "description", "message_id", mode="after")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("customer_name", mode="after")
    @classmethod
    def _name_required(cls, v):
        if not v:
            raise ValueError("customer_name is required")
        return v

    @field_validator("email", mode="after")
    @classmethod
    def _email_shape(cls, v):
        # A malformed address is dropped rather than refused: the work order still
        # has to become a card, and the address is kept in the note verbatim.
        return v if v and _EMAIL.match(v) else None


class AhsCancellationIn(BaseModel):
    ahs_job_id: str
    message_id: str | None = Field(None, max_length=500)
    received_at: datetime | None = None

    model_config = {"extra": "ignore"}

    _id = field_validator("ahs_job_id", mode="after")(_job_id)


# ---------------------------------------------------------------- pieces

def title_for(body: AhsJobIn) -> str:
    """GoHighLevel's name for the card, built the way owen-main's
    `emails.build_opportunity_body` builds it."""
    header = " ".join(x for x in (body.ahs_job_id, body.service) if x)
    return ("%s - %s" % (header, body.customer_name))[:TITLE_MAX].strip()


def split_name(full: str) -> tuple[str, str]:
    parts = full.split()
    if not parts:
        return "", ""
    return parts[0][:120], " ".join(parts[1:])[:120]


def split_address(raw: str | None) -> dict:
    """The contact's four address columns from one AHS address string.

    Dispatch writes "14436 SW 95TH LN MIAMI, FL 33186": no comma between the street
    and the city, so the city cannot be told apart from the street's last word. The
    rule is the Workiz importer's: anything not confidently identified stays in
    `address_street`. Only a trailing ", ST 12345" is split off, and a
    "street, city, ST 12345" shape gives up its city.
    """
    if not raw:
        return {}
    out = {"address_street": raw[:255]}
    m = _STATE_ZIP.search(raw)
    if not m:
        return out
    head = raw[:m.start()].strip()
    out["address_state"] = m.group(1).upper()
    out["address_postal_code"] = m.group(2)
    if "," in head:
        street, city = head.rsplit(",", 1)
        if street.strip() and city.strip():
            out["address_street"] = street.strip()[:255]
            out["address_city"] = city.strip()[:120]
            return out
    out["address_street"] = head[:255] or raw[:255]
    return out


def _jobs_count(db: Session) -> int:
    return db.scalar(select(func.count(Job.id))) or 0


def _lock(db: Session, job_id: str) -> None:
    """Serialise two deliveries of the same job number on PostgreSQL.

    There is no unique index on a JSON key to lean on, and adding one is a
    migration this feature does not need. owen-main drains its queue one job at a
    time, so this only matters for a retry racing its own first attempt; the
    transaction-scoped advisory lock makes that race lose cleanly. SQLite (tests)
    serialises writers on its own.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": zlib.crc32(("ahs_job:" + job_id).encode())})


def card_for(db: Session, job_id: str) -> Opportunity | None:
    """The card an AHS job number already has, oldest first."""
    return db.scalars(
        select(Opportunity)
        .where(Opportunity.custom_fields[AHS_JOB_ID].as_string() == job_id)
        .order_by(Opportunity.id)
    ).first()


def _board(db: Session, principal: auth.Principal) -> tuple[Pipeline, Stage]:
    """The AHS pipeline and its New Lead stage, refused out loud when either is
    missing, doubled, or hidden from the token's user.

    A 4xx, deliberately: owen-main does not retry a 4xx, and no retry fixes a board
    that is not there. The reason is recorded on the email over there.
    """
    pipelines = [p for p in db.scalars(select(Pipeline).where(
        Pipeline.name == PIPELINE_NAME).order_by(Pipeline.id)).all()
        if pipeline_access.can_see(db, principal, p.id)]
    if not pipelines:
        raise HTTPException(422, "pipeline %r not found" % PIPELINE_NAME)
    if len(pipelines) > 1:
        raise HTTPException(409, "%d pipelines are named %r — refusing to guess" % (
            len(pipelines), PIPELINE_NAME))
    pipeline = pipelines[0]
    stages = db.scalars(select(Stage).where(
        Stage.pipeline_id == pipeline.id, Stage.name == STAGE_NAME)).all()
    if not stages:
        raise HTTPException(422, "stage %r not found in %r" % (STAGE_NAME, PIPELINE_NAME))
    if len(stages) > 1:
        raise HTTPException(409, "%d stages in %r are named %r — refusing to guess" % (
            len(stages), PIPELINE_NAME, STAGE_NAME))
    return pipeline, stages[0]


def _match_contact(db: Session, body: AhsJobIn) -> tuple[Contact | None, str | None]:
    """(contact, how) — by phone on the last ten digits, then by email."""
    number = body.phone or ""
    if len(phone_match.digits(number)) >= phone_match.NATIONAL_DIGITS:
        found = db.scalars(select(Contact)
                           .where(phone_match.phone_clause(Contact.phone, number))
                           .order_by(Contact.id)).first()
        if found is not None:
            return found, "phone"
    if body.email:
        found = db.scalars(select(Contact)
                           .where(func.lower(Contact.email) == body.email.lower())
                           .order_by(Contact.id)).first()
        if found is not None:
            return found, "email"
    return None, None


def _opportunity_out(o: Opportunity) -> dict:
    return {"id": o.id, "title": o.title, "pipeline_id": o.pipeline_id,
            "stage_id": o.stage_id, "status": o.status, "value_cents": o.value_cents,
            "source": o.source, "created_by": o.created_by, "contact_id": o.contact_id}


# ---------------------------------------------------------------- the two writes

def deliver_job(db: Session, body: AhsJobIn,
                principal: auth.Principal) -> tuple[int, dict]:
    """(status code, payload). Flushes; the route commits. See the module docstring."""
    _lock(db, body.ahs_job_id)
    pipeline, stage = _board(db, principal)

    existing = card_for(db, body.ahs_job_id)
    if existing is not None:
        if not pipeline_access.can_see(db, principal, existing.pipeline_id):
            raise HTTPException(422, "pipeline %r not found" % PIPELINE_NAME)
        # A repeat delivery succeeded: the card is there. 200 with the row, never a
        # 409, so a retry of a request that committed before its answer got home
        # completes the job on the far side instead of dead-lettering it.
        return 200, {"outcome": "existing", "ahs_job_id": body.ahs_job_id,
                     "opportunity": _opportunity_out(existing),
                     "contact": {"id": existing.contact_id, "matched_by": None},
                     "note_id": None, "automation": AUTOMATION}

    jobs_before = _jobs_count(db)

    contact, how = _match_contact(db, body)
    if contact is None:
        first, last = split_name(body.customer_name)
        contact = Contact(first_name=first, last_name=last, phone=store_phone(body.phone),
                          email=body.email, source=SOURCE, created_by=CREATED_BY,
                          **split_address(body.service_address))
        db.add(contact)
        db.flush()
        how = "created"

    position = db.scalar(select(func.count(Opportunity.id))
                         .where(Opportunity.stage_id == stage.id)) or 0
    card = Opportunity(title=title_for(body), contact_id=contact.id,
                       pipeline_id=pipeline.id, stage_id=stage.id,
                       value_cents=body.value_cents, status="open", position=position,
                       source=SOURCE, created_by=CREATED_BY,
                       custom_fields={AHS_JOB_ID: body.ahs_job_id})
    db.add(card)
    db.flush()

    note_body = body.description or "AHS job %s\nCustomer: %s" % (
        body.ahs_job_id, body.customer_name)
    note = OpportunityNote(opportunity_id=card.id, body=note_body[:NOTE_MAX],
                           created_by_id=principal.user_id)
    db.add(note)
    db.flush()

    if _jobs_count(db) != jobs_before:
        # Nothing on this path may queue a text, a reminder or a notification.
        # Raised rather than asserted so it survives `python -O`; the route's
        # session is never committed, so the contact and the card go with it.
        raise RuntimeError("an AHS work order enqueued a job; refusing to commit")

    return 201, {"outcome": "created", "ahs_job_id": body.ahs_job_id,
                 "opportunity": _opportunity_out(card),
                 "contact": {"id": contact.id, "matched_by": how},
                 "note_id": note.id, "automation": AUTOMATION}


def deliver_cancellation(db: Session, body: AhsCancellationIn,
                         principal: auth.Principal) -> dict:
    """Note the cancellation on that job's card and leave the card exactly as it is.

    Closing it would be a judgement about work nobody here can see — the crew may
    already have been paid, or the job re-dispatched under a new number. The same
    call owen-main's GoHighLevel relay makes.
    """
    _lock(db, body.ahs_job_id)
    card = card_for(db, body.ahs_job_id)
    if card is None or not pipeline_access.can_see(db, principal, card.pipeline_id):
        return {"outcome": "no_card", "ahs_job_id": body.ahs_job_id,
                "opportunity_id": None, "note_id": None, "automation": AUTOMATION}

    marker = CANCELLED_PREFIX + body.ahs_job_id
    already = db.scalars(select(OpportunityNote).where(
        OpportunityNote.opportunity_id == card.id,
        OpportunityNote.body.startswith(marker + " (")).order_by(OpportunityNote.id)).first()
    if already is not None:
        return {"outcome": "already_noted", "ahs_job_id": body.ahs_job_id,
                "opportunity_id": card.id, "note_id": already.id,
                "automation": AUTOMATION}

    jobs_before = _jobs_count(db)
    when = body.received_at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    note = OpportunityNote(
        opportunity_id=card.id, created_by_id=principal.user_id,
        body="%s (%s)\n\nAmerican Home Shield cancelled this dispatch. The card has "
             "been left open and unchanged — check whether the work was already done, "
             "or whether it was re-dispatched under a new job number." % (
                 marker, when.astimezone(ACCOUNT_TZ).date().isoformat()))
    db.add(note)
    db.flush()
    if _jobs_count(db) != jobs_before:
        raise RuntimeError("an AHS cancellation enqueued a job; refusing to commit")
    return {"outcome": "noted", "ahs_job_id": body.ahs_job_id,
            "opportunity_id": card.id, "note_id": note.id, "automation": AUTOMATION}
