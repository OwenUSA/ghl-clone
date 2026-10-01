"""Every call and text with a customer number, from both systems, keyed by the number's last ten
digits — the one join that works across Quo, the CRM's own line and Zuper Connect.

  * Quo (OpenPhone) calls and texts, and the CRM's own line: already in this database
    (`conversation_events` on a contact's thread, `number_thread_events` on a number nobody
    holds). Quo's own summary rides at the end of a call's body as " Summary: …".
  * Zuper Connect calls: `dispatch_calls`, filled by the reader.

A text that never left (refused, failed, logged only) is not contact with the customer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Contact,
    Conversation,
    ConversationEvent,
    DispatchCall,
    EventType,
    NumberThread,
    NumberThreadEvent,
)
from . import config

NOT_SENT = {"FAILED", "REFUSED", "LOGGED_ONLY"}


@dataclass
class Comm:
    at: datetime
    kind: str                 # call | text
    out: bool                 # we called / texted them
    talked: bool              # an answered call of TALK_SECONDS or more
    missed: bool              # their call nobody answered
    seconds: int
    summary: str
    source: str               # Quo | Zuper | CRM line
    name: str | None = None
    staff: str | None = None
    transcript: str = ""


def digits(value) -> str:
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 10 else ""


def summary_of(body: str | None) -> str:
    """Quo's summary of a call, out of the body the mirror wrote."""
    body = body or ""
    if "Summary:" in body:
        return body.split("Summary:", 1)[1].strip()
    return ""


def _value(enum_or_str) -> str:
    return getattr(enum_or_str, "value", enum_or_str) or ""


def _from_event(e, name: str | None) -> Comm:
    is_call = _value(e.type) == EventType.CALL.value
    out = _value(e.direction) == "OUTBOUND"
    secs = e.duration_seconds or 0
    status = (e.call_status or "").lower()
    answered = status == "completed" and secs > 0
    source = "Quo" if (e.source_system or "") == "OpenPhone" else "CRM line"
    return Comm(
        at=config.aware(e.occurred_at), kind="call" if is_call else "text", out=out,
        talked=is_call and status == "completed" and secs >= config.TALK_SECONDS,
        missed=is_call and not out and not answered,
        seconds=secs, summary=summary_of(e.body) if is_call else (e.body or "")[:500],
        source=source, name=name, transcript=(e.transcript or "")[:4000] if is_call else "")


def load(db: Session, since: datetime) -> dict[str, list[Comm]]:
    out: dict[str, list[Comm]] = {}

    def add(phone, comm: Comm) -> None:
        key = digits(phone)
        if key:
            out.setdefault(key, []).append(comm)

    types = (EventType.CALL, EventType.SMS)
    for e, phone, first, last in db.execute(
            select(ConversationEvent, Contact.phone, Contact.first_name, Contact.last_name)
            .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(ConversationEvent.type.in_(types), ConversationEvent.occurred_at >= since)):
        if _value(e.type) == EventType.SMS.value and _value(e.direction) == "OUTBOUND" \
                and _value(e.delivery_status) in NOT_SENT:
            continue
        add(phone, _from_event(e, " ".join(x for x in (first, last) if x).strip() or None))
    for e, phone, quo_name in db.execute(
            select(NumberThreadEvent, NumberThread.phone, NumberThread.quo_name)
            .join(NumberThread, NumberThread.id == NumberThreadEvent.number_thread_id)
            .where(NumberThreadEvent.type.in_(types), NumberThreadEvent.occurred_at >= since)):
        if _value(e.type) == EventType.SMS.value and _value(e.direction) == "OUTBOUND" \
                and _value(e.delivery_status) in NOT_SENT:
            continue
        add(phone, _from_event(e, quo_name))
    for c in db.scalars(select(DispatchCall).where(DispatchCall.occurred_at >= since)):
        secs = c.duration_seconds or 0
        done = (c.status or "").upper() == "COMPLETED"
        inbound = (c.direction or "").upper() != "OUTGOING"
        add(c.number, Comm(
            at=config.aware(c.occurred_at), kind="call", out=not inbound,
            talked=done and secs >= config.TALK_SECONDS, missed=inbound and not done,
            seconds=secs, summary=c.summary or "", source="Zuper", staff=c.staff_name))
    for comms in out.values():
        comms.sort(key=lambda c: c.at)
    return out
