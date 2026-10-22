"""Read every call and text ONCE and tag what matters - "still leaking", "come back", a
complaint, interest in more work, a payment problem, praise, a schedule change - each tag with
the exact words it came from. A tag whose quote is not in the text is dropped, so every number in
the report goes back to a sentence somebody said.

Sources (the same three `dispatch.comms` reads): Quo and the CRM line (`conversation_events`,
`number_thread_events`: a call's summary and transcript, a text's body) and Zuper Connect
(`dispatch_calls`: Zuper's summary, our transcript when there is one). Through the Dispatch AI's
connection (`provider_for(entry="insights")`): its switch, pause, daily cap and the registry's
limits apply; runs are `dispatch_ai_runs` of kind "insights".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ai import providers
from ..dispatch import ai as dai
from ..dispatch import config as dc
from ..dispatch.comms import NOT_SENT, summary_of
from ..inspection_docs.rules import quote_ok
from ..models import (
    Contact,
    Conversation,
    ConversationEvent,
    ConversationRead,
    ConversationSignal,
    DispatchCall,
    DispatchJob,
    EventType,
    NumberThread,
    NumberThreadEvent,
)
from . import config as c

KIND = "insights"
SYSTEM = (
    "You read short call summaries, call transcripts and text messages between a roofing "
    "company (Dream Team Roofing) and its customers. For each item, list ONLY the tags that "
    "clearly apply:\n"
    "  still_leaking     the customer says the roof still leaks / leaks again after our work\n"
    "  callback_request  the customer asks us to come back, re-check or fix our work\n"
    "  complaint         the customer is unhappy with us (service, wait, quality, staff)\n"
    "  upsell_interest   the customer wants more work quoted (new roof, gutters, extra area)\n"
    "  payment_issue     a problem with paying, a dispute, a refused charge, cannot pay\n"
    "  praise            the customer thanks or praises the work or a person\n"
    "  schedule_change   the customer moves, cancels or asks to change a visit\n"
    "Each tag needs \"quote\": words copied EXACTLY from that item (5-25 words). No tag without "
    "a quote; nothing guessed. Answer JSON only: {\"<item id>\": [{\"tag\": \"...\", \"quote\": "
    "\"...\"}], ...}; an item with nothing to tag is omitted.")


@dataclass
class Item:
    ref: str
    source: str
    kind: str
    at: datetime
    phone: str
    inbound: bool
    text: str


def _value(x) -> str:
    return getattr(x, "value", x) or ""


def _event_item(e, phone: str, prefix: str) -> Item | None:
    is_call = _value(e.type) == EventType.CALL.value
    inbound = _value(e.direction) != "OUTBOUND"
    if not is_call and not inbound and _value(e.delivery_status) in NOT_SENT:
        return None
    if is_call:
        text = "\n".join(x for x in (summary_of(e.body), (e.transcript or "")[:4000]) if x)
    else:
        text = e.body or ""
    if len(text.strip()) < c.MIN_CHARS:
        return None
    return Item("%s:%d" % (prefix, e.id),
                "quo" if (e.source_system or "") == "OpenPhone" else "crm_line",
                "call" if is_call else "text", dc.aware(e.occurred_at), c.ten(phone), inbound,
                text.strip())


def candidates(db: Session, now: datetime, s: c.Settings) -> list[Item]:
    since = now - timedelta(days=c.LOOKBACK_DAYS)
    read = set(db.scalars(select(ConversationRead.source_ref)))
    out: list[Item] = []
    types = (EventType.CALL, EventType.SMS)
    for e, phone in db.execute(
            select(ConversationEvent, Contact.phone)
            .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(ConversationEvent.type.in_(types), ConversationEvent.occurred_at >= since)):
        it = _event_item(e, phone, "ce")
        if it:
            out.append(it)
    for e, phone in db.execute(
            select(NumberThreadEvent, NumberThread.phone)
            .join(NumberThread, NumberThread.id == NumberThreadEvent.number_thread_id)
            .where(NumberThreadEvent.type.in_(types), NumberThreadEvent.occurred_at >= since)):
        it = _event_item(e, phone, "nt")
        if it:
            out.append(it)
    for call in db.scalars(select(DispatchCall).where(DispatchCall.occurred_at >= since)):
        text = "\n".join(x for x in (call.summary or "", (call.transcript or "")[:4000]) if x)
        if len(text.strip()) >= c.MIN_CHARS:
            out.append(Item("zc:%s" % call.call_uid, "zuper_connect", "call",
                            dc.aware(call.occurred_at), c.ten(call.number),
                            (call.direction or "").upper() != "OUTGOING", text.strip()))
    skip = set(s.exclude_phones)
    out = [i for i in out if i.ref not in read and i.phone and i.phone not in skip]
    out.sort(key=lambda i: i.at)
    return out[:c.PER_RUN]


def job_for(db: Session, phone: str, cache: dict) -> DispatchJob | None:
    if phone in cache:
        return cache[phone]
    hits = [j for j in db.scalars(select(DispatchJob).where(DispatchJob.phones.isnot(None)))
            if phone in {c.ten(p) for p in j.phones or []}]
    hits.sort(key=lambda j: (not j.is_open, -(j.id or 0)))
    cache[phone] = hits[0] if hits else None
    return cache[phone]


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        out = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def tag(db: Session, now: datetime, s: c.Settings) -> dict:
    """Read what has not been read; -> {"read", "tags", "stopped"?}."""
    items = candidates(db, now, s)
    out = {"read": 0, "tags": 0}
    if not items:
        return out
    jobs: dict = {}
    for start in range(0, len(items), c.BATCH):
        batch = items[start:start + c.BATCH]
        try:
            prov, model = dai.provider_for(db, now, entry=c.KEY)
        except dai.Unavailable as e:
            return dict(out, stopped=str(e))
        payload = {str(i): {"kind": it.kind, "from": "customer" if it.inbound else "company",
                            "text": it.text[:3000]} for i, it in enumerate(batch)}
        try:
            turn = prov.complete(system=SYSTEM, tools=[], model=model, max_tokens=1500,
                                 messages=[{"role": "user", "content": json.dumps(
                                     payload, ensure_ascii=False)}])
        except providers.ProviderError as e:
            dai._record(db, KIND, model, None, error=e.sentence[:300])
            db.commit()
            return dict(out, stopped=e.sentence)
        dai._record(db, KIND, model, turn)
        answer = _json(turn.text)
        for i, it in enumerate(batch):
            found = answer.get(str(i)) if isinstance(answer.get(str(i)), list) else []
            n = 0
            j = job_for(db, it.phone, jobs)
            for f in found:
                if not isinstance(f, dict) or f.get("tag") not in c.TAGS:
                    continue
                quote = " ".join(str(f.get("quote") or "").split())[:500]
                if not quote_ok(quote, it.text):
                    continue                          # an invented quote: dropped
                sig = ConversationSignal(
                    source=it.source, source_ref=it.ref, kind=it.kind, at=it.at,
                    phone=it.phone, inbound=it.inbound, tag=f["tag"], quote=quote,
                    job_uid=j.job_uid if j else None, job_number=j.job_number if j else None,
                    board=j.board if j else None, customer_name=j.customer_name if j else None,
                    technician=(j.technician or (j.assigned or [None])[0]) if j else None)
                try:
                    with db.begin_nested():
                        db.add(sig)
                        db.flush()
                    n += 1
                except IntegrityError:
                    pass
            db.add(ConversationRead(source_ref=it.ref, read_at=now, tags=n))
            out["read"] += 1
            out["tags"] += n
        db.commit()
    return out
