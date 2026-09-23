"""Who is this caller, and what is their job? — the voice agent's customer brief (2026-09-24).

Phase 2a of the voice-agent amendment (DECISIONS.md, 2026-09-22, decision 3: "Every agent gets
the customer brief when the caller is known, whichever number they rang: name, stage, next
appointment"). owen-main asks this ONE endpoint as a voice call is answered, and whatever comes
back is read by a language model that is speaking to the caller. So the question this module
answers is not "what does the CRM know" but "what may a voice agent say out loud to whoever
is holding that phone".

    owen-main ──Bearer ghl_pat_ (events:write)──▶ POST /api/agent-context {"caller_number"}

## The answer, and nothing else

An unknown number is `{"known": false}` — no other key, so a miss can never carry a hint
about anybody. A known caller gets exactly:

    known, contact{first_name, last_name},
    opportunity{title, stage, pipeline}   their most recently updated OPEN card, or null
    next_appointment{starts_at, title}    the next visit still on, or null
    last_contact_at                       the last call / text / email on their thread, or null

Built field by field from named columns, never by serialising a row, so a column added to
`contacts` or `opportunities` later cannot reach a caller by accident. **Never** here: money
(`value_cents`), the Checklist and every other custom field, an opportunity's notes, a thread's
internal notes, email addresses, the address, other customers.

## Identity: last ten digits, exactly one contact

The shared rule (`app/phone_match.py`, the same one owen-main and `/api/events` use), and only
for a COMPLETE number: fewer than ten digits is `known: false`, never a substring match. Two
contacts holding the same line is also `known: false` — that is a household sharing a phone,
and greeting the wrong person by name is worse than greeting nobody (owen-main's
CRM_CONTEXT_SPEC C3). No fuzzy matching, no guessing between them.

## Who may ask

The feed's machine token, gated like `POST /api/events` (`auth.EVENTS_INGEST`, scope
`events:write`, listed in `auth.EVENTS_WRITE_PATHS`): ADMIN or DISPATCHER only, so a TECH who
mints themselves a token never reaches it. The body carries a phone number and nothing else —
never a contact id, so the caller of this endpoint cannot choose whose brief it reads.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import assigned_access, auth, automations, phone_match, pipeline_access
from .db import get_db
from .models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    EventType,
    Opportunity,
    Pipeline,
    Stage,
    utcnow,
)

router = APIRouter(tags=["agent-context"])

PATH = "/api/agent-context"

UNKNOWN: dict = {"known": False}

# "When we last spoke to them": the thread entries that ARE talking to the customer. Internal
# notes (`automations.INTERNAL_TYPES`, NOTE and INTERNAL_COMMENT) are not among them, and the
# activity rows (appointment booked, stage changed) are not contact either.
#
# Internal notes and `auth.sees_internal`: that predicate says who may READ a note, and it is
# True for every principal that can reach this route (EVENTS_INGEST admits ADMIN and DISPATCHER
# only). It is deliberately not what decides this answer, because the reader here is not the
# token's owner — it is a voice agent talking to whoever is on the phone. So no note, of either
# kind, is ever selected: not its body, not its existence, not even its timestamp.
SPOKEN_TYPES = (EventType.CALL, EventType.SMS, EventType.EMAIL, EventType.WHATSAPP)
# An outbound message that never left is not "we spoke to them".
NEVER_SENT = (DeliveryStatus.REFUSED, DeliveryStatus.FAILED, DeliveryStatus.LOGGED_ONLY)



class AgentContextIn(BaseModel):
    # `extra="forbid"`: a contact id, a name, anything but the number is refused (422) rather
    # than silently ignored, so nobody builds a client that believes it can pick the contact.
    model_config = ConfigDict(extra="forbid")
    caller_number: str = Field(..., max_length=40)


def _iso(dt: datetime | None) -> str | None:
    # SQLite hands back naive datetimes from timezone=True columns; everything is stored UTC.
    return None if dt is None else auth.as_aware(dt).isoformat()


def _one_contact(db: Session, number: str) -> Contact | None:
    """The ONE contact whose phone is this line, or None — for no match AND for two."""
    if len(phone_match.digits(number)) < phone_match.NATIONAL_DIGITS:
        return None
    if not phone_match.looks_like_phone(number):
        return None
    rows = db.scalars(
        select(Contact).where(phone_match.phone_clause(Contact.phone, number))
        .order_by(Contact.id).limit(2)
    ).all()
    return rows[0] if len(rows) == 1 else None


def brief(db: Session, principal: auth.Principal, number: str) -> dict:
    """The whole answer. Separate from the route so it can be read and tested on its own."""
    contact = _one_contact(db, number)
    if contact is None:
        return dict(UNKNOWN)

    # PIPELINE ACCESS — the rule applied, and why. This is a machine token, but it belongs to a
    # user, and `pipeline_access.hidden_pipeline_ids` answers for that user exactly as it does
    # for the board: an ADMIN-owned token sees every pipeline, a DISPATCHER-owned one does not
    # see a pipeline restricted to other named users. Every deal and every visit below is
    # filtered through that one set — a visit counts only if neither its deal nor its calendar
    # sits on a hidden pipeline — so a restriction the owner set on the board cannot be read
    # back out of a phone call. It is the SAME function every staff route uses; the feed is
    # not a second, wider door.
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)

    opp_row = db.execute(
        pipeline_access.visible_opportunities(
            select(Opportunity.title, Stage.name, Pipeline.name)
            .join(Stage, Stage.id == Opportunity.stage_id)
            .join(Pipeline, Pipeline.id == Opportunity.pipeline_id)
            .where(Opportunity.contact_id == contact.id, Opportunity.status == "open"),
            hidden)
        .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
        .limit(1)
    ).first()

    visit_stmt = (
        select(Appointment.starts_at, Appointment.title)
        .outerjoin(Opportunity, Opportunity.id == Appointment.opportunity_id)
        .outerjoin(Calendar, Calendar.id == Appointment.calendar_id)
        .where(Appointment.contact_id == contact.id,
               Appointment.status.not_in(automations.NO_REMINDER_STATUSES),
               Appointment.starts_at >= utcnow())
    )
    if hidden:
        visit_stmt = visit_stmt.where(
            or_(Appointment.opportunity_id.is_(None), Opportunity.pipeline_id.not_in(hidden)),
            or_(Calendar.pipeline_id.is_(None), Calendar.pipeline_id.not_in(hidden)),
        )
    visit = db.execute(visit_stmt.order_by(Appointment.starts_at, Appointment.id)
                       .limit(1)).first()

    last = db.scalar(
        select(func.max(ConversationEvent.occurred_at))
        .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
        .where(Conversation.contact_id == contact.id,
               ConversationEvent.type.in_(SPOKEN_TYPES),
               ~and_(ConversationEvent.direction == Direction.OUTBOUND,
                     ConversationEvent.delivery_status.is_not(None),
                     ConversationEvent.delivery_status.in_(NEVER_SENT)))
    )

    return {
        "known": True,
        "contact": {"first_name": contact.first_name or "",
                    "last_name": contact.last_name or ""},
        "opportunity": None if opp_row is None else {
            "title": opp_row[0], "stage": opp_row[1], "pipeline": opp_row[2]},
        "next_appointment": None if visit is None else {
            "starts_at": _iso(visit[0]), "title": visit[1]},
        "last_contact_at": _iso(last),
    }


@router.post(PATH)
def agent_context(body: AgentContextIn, db: Session = Depends(get_db),
                  principal: auth.Principal = auth.EVENTS_INGEST) -> dict:
    """The customer brief for a voice agent: `{"known": false}`, or four facts about ONE
    contact. Read-only; writes nothing, queues nothing. Pipeline access through
    `pipeline_access.hidden_pipeline_ids` — see `brief`."""
    # "Only assigned data" is a rule about a PERSON's screen and does not describe a feed. But
    # a token inherits its owner's switch, and a limited dispatcher's token must not become a
    # way to read any caller's job by phone number — so it is refused, not narrowed.
    if assigned_access.restricted(principal):
        raise HTTPException(403, "a user limited to their own jobs cannot look up callers")
    return brief(db, principal, body.caller_number)
