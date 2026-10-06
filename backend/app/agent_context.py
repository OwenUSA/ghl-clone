"""Who is this caller, and what is their job? — the voice agent's customer brief (2026-09-24).

Phase 2a of the voice-agent amendment (DECISIONS.md, 2026-09-22, decision 3: "Every agent gets
the customer brief when the caller is known, whichever number they rang: name, stage, next
appointment"). owen-main asks this ONE endpoint as a voice call is answered, and whatever comes
back is read by a language model that is speaking to the caller. So the question this module
answers is not "what does the CRM know" but "what may a voice agent say out loud to whoever
is holding that phone".

    owen-main ──Bearer ghl_pat_ (events:write)──▶ POST /api/agent-context
                                                  {"caller_number", "agent_name"?}

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
internal notes, email addresses, other customers. (The address only by the agent's own
`address` switch — below.)

## Identity: last ten digits, exactly one contact

The shared rule (`app/phone_match.py`, the same one owen-main and `/api/events` use), and only
for a COMPLETE number: fewer than ten digits is `known: false`, never a substring match. Two
contacts holding the same line is also `known: false` — that is a household sharing a phone,
and greeting the wrong person by name is worse than greeting nobody (owen-main's
CRM_CONTEXT_SPEC C3). No fuzzy matching, no guessing between them.

## The agent's own switches (2026-10-06, docs/RETELL-PLAN.md C2)

With `agent_name` naming a non-archived VOICE agent, the answer is built from that agent's
PUBLISHED "Customer information this agent receives" switches (`voice.CONTEXT_SOURCES`, all
OFF by default — decision 7). Without `agent_name`, or with a name no voice agent has, the
answer is the one above, byte for byte. With a known agent:

    known, source ("crm" | "zuper")                               always
    contact, opportunity, next_appointment, last_contact_at       crm_card
    zuper_job{job_number, board, status, status_since,
              technician, scheduled_start} | null                 zuper_job
    recent_calls[{at, channel, direction, summary}]               call_summaries (quo,
                                                                  zuper_connect) / ai_calls (ai)
    recent_texts[{at, direction, text}]                           texts
    address "<street>, <city>" | null                             address

`recent_calls` is at most 3, newest first, the last 90 days, each summary ≤ 400 characters —
summaries that ALREADY exist (Quo's, Zuper Connect's, an earlier AI call's), no new AI spend
(decision 8). `recent_texts` is the last 3 texts actually sent or received, ≤ 200 characters
each — never a note, never a text that did not leave. The address is sent by the owner's
choice (decision 9): the agent compares it and is told never to read it back.

### Who counts as "registered" (decision 5) — the rule, exactly

  1. Fewer than ten digits, or not a phone number: unknown.
  2. Two or more CRM contacts on the line: unknown (a household), whatever Zuper says.
  3. Exactly one CRM contact: that contact (`source: "crm"`) — UNLESS a Zuper customer on the
     same line (`dispatch_jobs.phones`, last ten digits) is somebody else. A Zuper customer is
     the SAME person when the CRM links them (a `zuper_mappings` customer↔contact row, or one
     of that customer's jobs mirrors one of the contact's cards) or when Zuper's customer name
     is the contact's full name (case and spacing ignored). Any other Zuper customer on the
     line makes it two people: unknown. Greeting the wrong person is worse than greeting
     nobody.
  4. No CRM contact: exactly one Zuper customer on the line (by Zuper's customer uid, else its
     name) → `source: "zuper"`, `contact` from Zuper's customer name, `opportunity` and
     `next_appointment` null. Two customers: unknown. A customer whose every job sits on a
     board hidden from the token's owner is unknown too.

Zuper is only consulted with a known agent — the old answer never changes.

## Who may ask

The feed's machine token, gated like `POST /api/events` (`auth.EVENTS_INGEST`, scope
`events:write`, listed in `auth.EVENTS_WRITE_PATHS`): ADMIN or DISPATCHER only, so a TECH who
mints themselves a token never reaches it. The body carries a phone number and an agent's
name, nothing else — never a contact id, so the caller of this endpoint cannot choose whose
brief it reads, and the agent's switches are read from the CRM, never from the request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from . import assigned_access, auth, automations, phone_match, pipeline_access
from .db import get_db
from .models import (
    AiAgent,
    AiAgentVersion,
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    DispatchCall,
    DispatchJob,
    EventType,
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    Pipeline,
    Stage,
    ZuperMapping,
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

# The wider brief's limits (C2).
CALLS_MAX = 3
CALLS_DAYS = 90
SUMMARY_MAX = 400
TEXTS_MAX = 3
TEXT_MAX = 200
# How many recent calls are read to find three with a summary. Bounded, so a customer with a
# thousand calls costs the same as one with ten.
CALL_SCAN = 40
QUO_SOURCE = "OpenPhone"


class AgentContextIn(BaseModel):
    # `extra="forbid"`: a contact id, a name, anything but the number is refused (422) rather
    # than silently ignored, so nobody builds a client that believes it can pick the contact.
    # `agent_name` (2026-10-06) picks WHICH switches apply, never whose brief is read.
    model_config = ConfigDict(extra="forbid")
    caller_number: str = Field(..., max_length=40)
    agent_name: str | None = Field(None, max_length=200)


def _iso(dt: datetime | None) -> str | None:
    # SQLite hands back naive datetimes from timezone=True columns; everything is stored UTC.
    return None if dt is None else auth.as_aware(dt).isoformat()


def _complete(number: str) -> bool:
    return (len(phone_match.digits(number)) >= phone_match.NATIONAL_DIGITS
            and phone_match.looks_like_phone(number))


def _contacts(db: Session, number: str, limit: int = 2) -> list[Contact]:
    return list(db.scalars(
        select(Contact).where(phone_match.phone_clause(Contact.phone, number))
        .order_by(Contact.id).limit(limit)
    ).all())


def _one_contact(db: Session, number: str) -> Contact | None:
    """The ONE contact whose phone is this line, or None — for no match AND for two."""
    if not _complete(number):
        return None
    rows = _contacts(db, number)
    return rows[0] if len(rows) == 1 else None


# ------------------------------------------------------------------ the CRM card's facts

def _opportunity(db: Session, contact_id: int, hidden: set[int]) -> dict | None:
    row = db.execute(
        pipeline_access.visible_opportunities(
            select(Opportunity.title, Stage.name, Pipeline.name)
            .join(Stage, Stage.id == Opportunity.stage_id)
            .join(Pipeline, Pipeline.id == Opportunity.pipeline_id)
            .where(Opportunity.contact_id == contact_id, Opportunity.status == "open"),
            hidden)
        .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
        .limit(1)
    ).first()
    return None if row is None else {"title": row[0], "stage": row[1], "pipeline": row[2]}


def _next_visit(db: Session, contact_id: int, hidden: set[int]) -> dict | None:
    stmt = (
        select(Appointment.starts_at, Appointment.title)
        .outerjoin(Opportunity, Opportunity.id == Appointment.opportunity_id)
        .outerjoin(Calendar, Calendar.id == Appointment.calendar_id)
        .where(Appointment.contact_id == contact_id,
               Appointment.status.not_in(automations.NO_REMINDER_STATUSES),
               Appointment.starts_at >= utcnow())
    )
    if hidden:
        stmt = stmt.where(
            or_(Appointment.opportunity_id.is_(None), Opportunity.pipeline_id.not_in(hidden)),
            or_(Calendar.pipeline_id.is_(None), Calendar.pipeline_id.not_in(hidden)),
        )
    visit = db.execute(stmt.order_by(Appointment.starts_at, Appointment.id).limit(1)).first()
    return None if visit is None else {"starts_at": _iso(visit[0]), "title": visit[1]}


def _spoken(model):
    return and_(model.type.in_(SPOKEN_TYPES),
                ~and_(model.direction == Direction.OUTBOUND,
                      model.delivery_status.is_not(None),
                      model.delivery_status.in_(NEVER_SENT)))


def _last_contact(db: Session, contact_id: int) -> datetime | None:
    return db.scalar(
        select(func.max(ConversationEvent.occurred_at))
        .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
        .where(Conversation.contact_id == contact_id, _spoken(ConversationEvent))
    )


def brief(db: Session, principal: auth.Principal, number: str) -> dict:
    """The answer with no agent named (2026-09-24). Separate from the route so it can be read
    and tested on its own. UNCHANGED by the 2026-10-06 work — pinned byte for byte."""
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
    return {
        "known": True,
        "contact": {"first_name": contact.first_name or "",
                    "last_name": contact.last_name or ""},
        "opportunity": _opportunity(db, contact.id, hidden),
        "next_appointment": _next_visit(db, contact.id, hidden),
        "last_contact_at": _iso(_last_contact(db, contact.id)),
    }


# ------------------------------------------------------------------ the agent and its switches

def find_agent(db: Session, agent_name: str | None) -> tuple[AiAgent | None, dict]:
    """(the voice agent owen-main named, its PUBLISHED switches). The name is matched against
    the phone-system agent a version publishes to (`owen_agent`) first, then the CRM name, case
    and spacing ignored. No published version = every switch off. No such agent = (None, {})."""
    from .ai import config as ai_config
    from .ai import voice

    wanted = " ".join((agent_name or "").split()).lower()
    if not wanted:
        return None, {}
    agents = db.scalars(select(AiAgent).where(
        AiAgent.channel == AiAgent.VOICE, AiAgent.archived_at.is_(None))
        .order_by(AiAgent.id)).all()
    by_owen, by_name = None, None
    published: dict[int, dict] = {}
    for a in agents:
        v = db.get(AiAgentVersion, a.published_version_id) if a.published_version_id else None
        cfg = ai_config.merged(v.config) if v is not None else None
        published[a.id] = cfg or {}
        owen = " ".join(str((cfg or {}).get("owen_agent")
                             or (a.draft or {}).get("owen_agent") or "").split()).lower()
        if by_owen is None and owen == wanted:
            by_owen = a
        if by_name is None and " ".join(a.name.split()).lower() == wanted:
            by_name = a
    agent = by_owen or by_name
    if agent is None:
        return None, {}
    return agent, voice.context_sources(published.get(agent.id))


# ------------------------------------------------------------------ who is calling (decision 5)

def _norm_name(value: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (value or "").lower()).split())


@dataclass
class Caller:
    source: str                                  # crm | zuper
    contact: Contact | None = None
    zuper_name: str | None = None
    ten: str = ""
    # The caller's Zuper jobs the token's owner may see, best first.
    jobs: list[DispatchJob] = field(default_factory=list)


def _zuper_jobs(db: Session, ten: str) -> list[DispatchJob]:
    """Every Dispatch job (Zuper's copy) with this line among its customer's numbers. The
    JSON column is narrowed by its text first, then each row is checked exactly."""
    rows = db.scalars(select(DispatchJob).where(
        DispatchJob.phones.is_not(None),
        cast(DispatchJob.phones, Text).contains(ten, autoescape=True))).all()
    return [j for j in rows if ten in [str(p) for p in (j.phones or [])]]


def _customer_key(j: DispatchJob) -> str:
    if j.customer_uid:
        return "uid:" + j.customer_uid
    if _norm_name(j.customer_name):
        return "name:" + _norm_name(j.customer_name)
    return "job:" + j.job_uid


def _same_person(db: Session, contact: Contact, jobs: list[DispatchJob]) -> bool:
    """Is this Zuper customer (all `jobs` share one) the CRM contact? See the module's rule."""
    uids = {j.customer_uid for j in jobs if j.customer_uid}
    if uids and db.scalar(select(ZuperMapping.id).where(
            ZuperMapping.crm_type == "contact", ZuperMapping.crm_id == contact.id,
            ZuperMapping.zuper_type == "customer", ZuperMapping.zuper_uid.in_(uids))
            .limit(1)) is not None:
        return True
    job_uids = [j.job_uid for j in jobs]
    if job_uids and db.scalar(
            select(ZuperMapping.id)
            .join(Opportunity, Opportunity.id == ZuperMapping.crm_id)
            .where(ZuperMapping.crm_type == "opportunity", ZuperMapping.zuper_type == "job",
                   ZuperMapping.zuper_uid.in_(job_uids),
                   Opportunity.contact_id == contact.id).limit(1)) is not None:
        return True
    full = _norm_name("%s %s" % (contact.first_name or "", contact.last_name or ""))
    return bool(full) and any(_norm_name(j.customer_name) == full for j in jobs)


def _best_first(jobs: list[DispatchJob]) -> list[DispatchJob]:
    def when(j: DispatchJob):
        dt = j.status_since or j.zuper_created_at or j.first_seen_at
        return auth.as_aware(dt).timestamp() if dt else 0.0
    return sorted(jobs, key=lambda j: (bool(j.is_open), when(j), j.id), reverse=True)


def identify(db: Session, principal: auth.Principal, number: str) -> Caller | None:
    """The ONE customer on this line — a CRM contact or a Zuper-only customer — or None.
    The rule is the module docstring's "Who counts as registered"."""
    from .dispatch import alerts

    if not _complete(number):
        return None
    contacts = _contacts(db, number)
    if len(contacts) >= 2:
        return None
    ten = phone_match.digits(number)[-phone_match.NATIONAL_DIGITS:]
    zjobs = _zuper_jobs(db, ten)
    customers: dict[str, list[DispatchJob]] = {}
    for j in zjobs:
        customers.setdefault(_customer_key(j), []).append(j)
    hidden = alerts.hidden_boards(db, principal.user_id, principal.role) if zjobs else set()
    visible = _best_first([j for j in zjobs if not (j.board and j.board in hidden)])
    if contacts:
        contact = contacts[0]
        if any(not _same_person(db, contact, jobs) for jobs in customers.values()):
            return None
        return Caller("crm", contact=contact, ten=ten, jobs=visible)
    if len(customers) != 1 or not visible:
        return None
    name = next((j.customer_name for j in visible if (j.customer_name or "").strip()), None)
    return Caller("zuper", zuper_name=(name or "").strip() or None, ten=ten, jobs=visible)


# ------------------------------------------------------------------ the wider brief's parts

def _cut(text: str | None, limit: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def _direction(value) -> str:
    v = str(getattr(value, "value", value) or "").lower()
    return "outbound" if v.startswith("out") else "inbound"


def _threads(db: Session, caller: Caller):
    """(the event model, the where-clause for this caller's own thread(s))."""
    if caller.contact is not None:
        convs = select(Conversation.id).where(Conversation.contact_id == caller.contact.id)
        return ConversationEvent, ConversationEvent.conversation_id.in_(convs)
    thread = select(NumberThread.id).where(NumberThread.phone_key == caller.ten)
    return NumberThreadEvent, NumberThreadEvent.number_thread_id.in_(thread)


def _quo_summary(body: str | None) -> str:
    from .dispatch.comms import summary_of
    return summary_of(body)


def _recent_calls(db: Session, caller: Caller, *, quo_and_zuper: bool, ai: bool) -> list[dict]:
    since = utcnow() - timedelta(days=CALLS_DAYS)
    out: list[tuple[datetime, dict]] = []
    model, mine = _threads(db, caller)
    rows = db.execute(
        select(model.occurred_at, model.direction, model.body, model.source_system,
               model.ai_call)
        .where(mine, model.type == EventType.CALL, model.occurred_at >= since)
        .order_by(model.occurred_at.desc(), model.id.desc()).limit(CALL_SCAN)).all()
    for at, direction, body, source, ai_call in rows:
        summary, channel = "", None
        ai_summary = (ai_call or {}).get("summary") if isinstance(ai_call, dict) else None
        if ai_call and isinstance(ai_summary, str) and ai_summary.strip():
            if ai:
                summary, channel = ai_summary, "ai"
        elif quo_and_zuper and source == QUO_SOURCE:
            summary, channel = _quo_summary(body), "quo"
        if channel and summary.strip():
            out.append((auth.as_aware(at), {"at": _iso(at), "channel": channel,
                                            "direction": _direction(direction),
                                            "summary": _cut(summary, SUMMARY_MAX)}))
    if quo_and_zuper:
        for at, direction, summary in db.execute(
                select(DispatchCall.occurred_at, DispatchCall.direction, DispatchCall.summary)
                .where(DispatchCall.number == caller.ten, DispatchCall.occurred_at >= since,
                       DispatchCall.summary.is_not(None), DispatchCall.summary != "")
                .order_by(DispatchCall.occurred_at.desc()).limit(CALLS_MAX)).all():
            if (summary or "").strip():
                out.append((auth.as_aware(at), {"at": _iso(at), "channel": "zuper_connect",
                                                "direction": _direction(direction),
                                                "summary": _cut(summary, SUMMARY_MAX)}))
    out.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in out[:CALLS_MAX]]


def _recent_texts(db: Session, caller: Caller) -> list[dict]:
    model, mine = _threads(db, caller)
    rows = db.execute(
        select(model.occurred_at, model.direction, model.body)
        .where(mine, model.type == EventType.SMS, _spoken(model),
               model.body.is_not(None), model.body != "")
        .order_by(model.occurred_at.desc(), model.id.desc()).limit(TEXTS_MAX)).all()
    return [{"at": _iso(at), "direction": _direction(d), "text": _cut(body, TEXT_MAX)}
            for at, d, body in rows]


def _zuper_job(caller: Caller) -> dict | None:
    if not caller.jobs:
        return None
    j = caller.jobs[0]
    return {"job_number": j.job_number, "board": j.board, "status": j.status,
            "status_since": _iso(j.status_since), "technician": j.technician,
            "scheduled_start": _iso(j.scheduled_start)}


def _address(caller: Caller) -> str | None:
    c = caller.contact
    if c is not None and (c.address_street or "").strip():
        parts = [c.address_street.strip(), (c.address_city or "").strip()]
        return ", ".join(p for p in parts if p)
    for j in caller.jobs:
        street = (j.address or "").strip()
        if street:
            city = (j.city or "").strip()
            if city and city.lower() not in street.lower():
                return "%s, %s" % (street, city)
            return street
    return None


def _zuper_last_contact(db: Session, caller: Caller) -> datetime | None:
    model, mine = _threads(db, caller)
    times = [db.scalar(select(func.max(model.occurred_at)).where(mine, _spoken(model))),
             db.scalar(select(func.max(DispatchCall.occurred_at)).where(
                 DispatchCall.number == caller.ten))]
    times = [auth.as_aware(t) for t in times if t is not None]
    return max(times) if times else None


def _split_name(name: str | None) -> dict:
    parts = (name or "").split()
    return {"first_name": parts[0] if parts else "",
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else ""}


def agent_brief(db: Session, principal: auth.Principal, number: str, sources: dict) -> dict:
    """The answer for a known voice agent: only what its switches allow (C2)."""
    caller = identify(db, principal, number)
    if caller is None:
        return dict(UNKNOWN)
    out: dict = {"known": True, "source": caller.source}
    if sources.get("crm_card"):
        if caller.contact is not None:
            hidden = pipeline_access.hidden_pipeline_ids(db, principal)
            c = caller.contact
            out.update({
                "contact": {"first_name": c.first_name or "", "last_name": c.last_name or ""},
                "opportunity": _opportunity(db, c.id, hidden),
                "next_appointment": _next_visit(db, c.id, hidden),
                "last_contact_at": _iso(_last_contact(db, c.id)),
            })
        else:
            out.update({"contact": _split_name(caller.zuper_name), "opportunity": None,
                        "next_appointment": None,
                        "last_contact_at": _iso(_zuper_last_contact(db, caller))})
    if sources.get("zuper_job"):
        out["zuper_job"] = _zuper_job(caller)
    if sources.get("call_summaries") or sources.get("ai_calls"):
        out["recent_calls"] = _recent_calls(db, caller,
                                            quo_and_zuper=bool(sources.get("call_summaries")),
                                            ai=bool(sources.get("ai_calls")))
    if sources.get("texts"):
        out["recent_texts"] = _recent_texts(db, caller)
    if sources.get("address"):
        out["address"] = _address(caller)
    return out


@router.post(PATH)
def agent_context(body: AgentContextIn, db: Session = Depends(get_db),
                  principal: auth.Principal = auth.EVENTS_INGEST) -> dict:
    """The customer brief for a voice agent: `{"known": false}`, or what the named agent's
    switches allow about ONE customer (no agent named: the four 2026-09-24 facts). Read-only;
    writes nothing, queues nothing. Pipeline access through
    `pipeline_access.hidden_pipeline_ids`, Zuper boards through `alerts.hidden_boards`."""
    # "Only assigned data" is a rule about a PERSON's screen and does not describe a feed. But
    # a token inherits its owner's switch, and a limited dispatcher's token must not become a
    # way to read any caller's job by phone number — so it is refused, not narrowed.
    if assigned_access.restricted(principal):
        raise HTTPException(403, "a user limited to their own jobs cannot look up callers")
    agent, sources = find_agent(db, body.agent_name)
    if agent is None:
        return brief(db, principal, body.caller_number)
    return agent_brief(db, principal, body.caller_number, sources)
