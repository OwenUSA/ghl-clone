"""A text to a caller who left Anisa's call before being helped (2026-10-22, owner).

The owner's request: when a customer drops the call before reaching a person, or hangs up while
still with the agent, text them that we could not take the call and will call back. His words:
"Sorry we couldn't take your call, we will call you back as soon as possible." Live for everyone,
from the Zuper line (954) 420-7373 - a scoped lift of "no automatic texts" for this text only.

    POST /api/events (an inbound CALL carrying `ai_call`)
        └─ maybe_enqueue() - ONE `dropped_call_text` job, DELAY_SECONDS later (Retell's summary
           and analysis arrive in later reports, merged onto the same row)
              └─ worker: handle_job() decides and sends at most one text

Who gets it - the AI decides from Retell's summary, who hung up, "successful" and the transfer:
  * YES: the caller hung up right away or mid-conversation, said nothing, or a transfer nobody
    answered and they left;
  * NO: their request was handled, they said they needed nothing, a person took the transfer,
    or a sales / spam call.
Guards (each in tests/test_dropped_call.py):
  * only INBOUND AI calls, fresh ones; a transfer a person answered is never texted;
  * never a number that texted STOP (in the CRM, or in its Zuper conversation);
  * never twice in a day to one number (this text, the auto-reply, the call follow-up);
  * never after a PERSON already called or texted them back since the call;
  * 8 AM - 8 PM Mon-Sat: a call at night waits for 8 AM;
  * recorded BEFORE it is sent, never retried; the AI unavailable = no text (never a guess).
How it is sent: from the Zuper line through `client.dropped_call_write()` when the caller is
exactly one Zuper customer (Zuper can only text a customer); otherwise from the CRM line through
the ordinary transport (`automations.send_outbound*`), which owen-main still checks for STOP.
Off unless DROPPED_CALL_TEXTS_ENABLED; registry entry `dropped_call_text`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import automations, number_threads
from .ai import providers
from .ai.providers import REASONING_ROOM
from .autoreply import rules as arules
from .dispatch import ai as dai
from .dispatch import comms as dcomms
from .dispatch import config as dc
from .followup import config as fconf
from .models import (
    AgentRegistryLog,
    AutoReply,
    CallFollowup,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchJob,
    EventType,
    NumberThreadEvent,
)
from .queue import enqueue, utcnow
from .registry import catalog as rcat
from .registry import service as registry
from .zuper import client

log = logging.getLogger("dropped_call")
KEY = "dropped_call_text"
JOB_TYPE = "dropped_call_text"
DELAY_SECONDS = 180
ENV = "DROPPED_CALL_TEXTS_ENABLED"
TEXT = ("Sorry we couldn't take your call, we will call you back as soon as possible. "
        "- Dream Team Roofing. Reply STOP to opt out.")
SYSTEM = (
    "You read the end of a phone call that an AI receptionist (Anisa, Dream Team Roofing) "
    "answered. Decide whether the caller LEFT BEFORE BEING HELPED: they hung up right away or "
    "in the middle, said nothing, could not get what they needed, or asked for a person and "
    "nobody answered. Answer NO when the call reached its end - the request was handled, a "
    "message or appointment was taken, they said they did not need anything, a person took the "
    "call - or it is a sales, robocall or wrong-number call. JSON only: "
    "{\"text\": true|false, \"why\": \"one short reason\"}.")


def enabled() -> bool:
    return (os.getenv(ENV, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _key(ev) -> str:
    if ev.dedupe_key:
        return ev.dedupe_key
    return "%s%s" % ("n" if isinstance(ev, NumberThreadEvent) else "c", ev.id)


def maybe_enqueue(db: Session, ev, caller_number: str | None = None) -> str:
    if not enabled():
        return "off"
    if ev is None or ev.type is not EventType.CALL or not isinstance(ev.ai_call, dict) \
            or not ev.ai_call:
        return "not an AI call"
    if ev.direction is not Direction.INBOUND:
        return "not an inbound call"
    if not automations.is_fresh(ev.occurred_at):
        return "an old call"
    k = _key(ev)
    job = enqueue(db, JOB_TYPE, {
        "event_id": ev.id, "event_kind": "number" if isinstance(ev, NumberThreadEvent)
        else "contact", "event_key": k, "caller_number": caller_number or None},
        run_after=utcnow() + timedelta(seconds=DELAY_SECONDS),
        dedupe_key=("dropped_call:" + k)[:200])
    if job is None:
        return "already queued"
    job.max_attempts = 1                     # a retry after a send is a second text
    return "queued"


# ------------------------------------------------------------------------- deciding

def _seconds(ai: dict) -> int | None:
    for k in ("duration_s", "duration_seconds", "seconds"):
        v = ai.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return None


def pre_check(ai: dict) -> str | None:
    """A reason NOT to text, decided without the AI, or None."""
    t = ai.get("transfer") if isinstance(ai.get("transfer"), dict) else {}
    if t and t.get("outcome") in ("answered", "handled") and not t.get("returned_to_agent"):
        return "a person took the transfer"
    if ai.get("disconnection_reason") == "agent_hangup" and ai.get("successful") is True:
        return "the agent finished the call"
    return None


def decide(db: Session, ai: dict, now: datetime) -> tuple[bool | None, str]:
    """(text?, why). None = could not decide (no AI): never text on a guess."""
    why = pre_check(ai)
    if why:
        return False, why
    facts = {"summary": ai.get("summary") or "(no summary)",
             "who_hung_up": ai.get("disconnection_reason"),
             "agent_marked_successful": ai.get("successful"),
             "transfer": ai.get("transfer") or None, "seconds": _seconds(ai)}
    try:
        prov, model = dai.provider_for(db, now, entry=KEY)
        turn = prov.complete(system=SYSTEM, tools=[], model=model, max_tokens=REASONING_ROOM,
                             think=False,
                             messages=[{"role": "user", "content": json.dumps(facts)}])
    except (dai.Unavailable, providers.ProviderError) as e:
        return None, "the AI could not decide: %s" % e
    dai._record(db, "dropped_call", model, turn)
    m = re.search(r"\{.*\}", turn.text or "", re.DOTALL)
    try:
        got = json.loads(m.group(0)) if m else {}
    except ValueError:
        got = {}
    if not isinstance(got.get("text"), bool):
        return None, "the AI's answer was unreadable"
    return got["text"], str(got.get("why") or "")[:200]


# ------------------------------------------------------------------------- guards

def _find(db: Session, payload: dict):
    model = NumberThreadEvent if payload.get("event_kind") == "number" else ConversationEvent
    ev = db.get(model, payload.get("event_id")) if payload.get("event_id") else None
    if ev is not None and _key(ev) == payload.get("event_key"):
        return ev
    k = payload.get("event_key") or ""
    if k and not re.fullmatch(r"[nc]\d+", k):
        for m in (ConversationEvent, NumberThreadEvent):
            found = db.scalar(select(m).where(m.dedupe_key == k))
            if found is not None:
                return found
    return ev


def _phone(db: Session, ev, payload: dict) -> str:
    if isinstance(ev, ConversationEvent):
        conv = db.get(Conversation, ev.conversation_id)
        contact = db.get(Contact, conv.contact_id) if conv else None
        if contact is not None and contact.phone:
            return fconf.ten(contact.phone)
    return fconf.ten(payload.get("caller_number") or "")


def opted_out_here(db: Session, phone: str) -> bool:
    """STOP texted to us on this number's CRM threads (its contact's, its number thread), and
    no START since."""
    from .models import NumberThread
    rows = []
    contact = number_threads.contact_holding(db, phone)
    if contact is not None:
        rows += list(db.scalars(select(ConversationEvent).join(
            Conversation, Conversation.id == ConversationEvent.conversation_id).where(
            Conversation.contact_id == contact.id, ConversationEvent.type == EventType.SMS,
            ConversationEvent.direction == Direction.INBOUND)))
    key = number_threads.phone_key("+1" + phone)
    thread = db.scalar(select(NumberThread).where(NumberThread.phone_key == key)) if key \
        else None
    if thread is not None:
        rows += list(db.scalars(select(NumberThreadEvent).where(
            NumberThreadEvent.number_thread_id == thread.id,
            NumberThreadEvent.type == EventType.SMS,
            NumberThreadEvent.direction == Direction.INBOUND)))
    state = False
    for e in sorted(rows, key=lambda e: dc.aware(e.occurred_at)):
        if arules.is_stop(e.body):
            state = True
        elif arules.is_start(e.body):
            state = False
    return state


def texted_today(db: Session, phone: str, now: datetime) -> bool:
    since = now - timedelta(hours=24)
    if db.scalar(select(AgentRegistryLog.id).where(
            AgentRegistryLog.entry_key == KEY, AgentRegistryLog.target == phone,
            AgentRegistryLog.what == "text", AgentRegistryLog.at >= since).limit(1)):
        return True
    if db.scalar(select(AutoReply.id).where(
            AutoReply.phone == phone, AutoReply.state == "sent",
            AutoReply.created_at >= since).limit(1)):
        return True
    return bool(db.scalar(select(CallFollowup.id).where(
        CallFollowup.phone == phone, CallFollowup.text_state == "sent",
        CallFollowup.created_at >= since).limit(1)))


def person_reached_them(db: Session, phone: str, call_at: datetime) -> bool:
    for x in dcomms.load(db, call_at).get(phone, []):
        if dc.aware(x.at) > call_at and not x.ai and x.out and (x.kind == "text" or x.answered):
            return True
    return False


def zuper_customer(db: Session, phone: str) -> tuple[str | None, str | None]:
    """(customer uid, a job uid) when exactly one Zuper customer holds the number."""
    jobs = [j for j in db.scalars(select(DispatchJob).where(
        DispatchJob.customer_uid.isnot(None), DispatchJob.phones.isnot(None)))
        if phone in {fconf.ten(p) for p in j.phones or []}]
    uids = {j.customer_uid for j in jobs}
    if len(uids) != 1:
        return None, None
    best = min(jobs, key=lambda j: (not j.is_open, -(j.id or 0)))
    return best.customer_uid, best.job_uid


def next_send_time(now: datetime) -> datetime | None:
    """None = now is fine; else when the window opens (8:01 AM, Mon-Sat)."""
    if fconf.call_hours(now):
        return None
    t = dc.local(now)
    day = t.date() if t.hour < 8 else t.date() + timedelta(days=1)
    while True:
        start = datetime(day.year, day.month, day.day, 8, 1, tzinfo=dc.TZ)
        if fconf.call_hours(start):
            return start.astimezone(UTC)
        day += timedelta(days=1)


# ------------------------------------------------------------------------- sending

def _send_zuper(customer_uid: str, phone: str, job_uid: str | None) -> None:
    from .autoreply import zconnect as az
    problem = az.sender_check()
    if problem:
        raise client.ZuperError("refused", problem)
    with client.dropped_call_write():
        client.request("POST", az.SEND_PATH % customer_uid, body={
            "type": "SMS", "sms": {"body": TEXT, "to": "+1" + phone}})
        if job_uid:
            try:
                client.request("POST", client.path("job_notes", uid=job_uid), body={"note": {
                    "note": "Anisa (AI) answered this customer's call and they left before "
                            "being helped, so this text was sent from (954) 420-7373:\n\"%s\""
                            % TEXT, "is_private": True, "notify_users": False}})
            except client.ZuperError:
                log.warning("dropped_call: the note on job %s failed", job_uid)


def _send_crm(db: Session, phone: str) -> str:
    contact = number_threads.contact_holding(db, phone)
    if contact is not None:
        _, reason = automations.send_outbound(db, contact, TEXT)
    else:
        thread, _ = number_threads.thread_for_number(db, "+1" + phone)
        _, reason = automations.send_outbound_to_number(db, thread, TEXT)
    return reason


def handle_job(db: Session, payload: dict) -> None:
    try:
        _run(db, payload, datetime.now(UTC))
    except Exception:
        db.rollback()
        log.exception("dropped_call failed for %r", payload.get("event_key"))


def _record(db: Session, now: datetime, phone: str, what: str, reason: str,
            body: str | None = None) -> None:
    db.add(AgentRegistryLog(at=now, entry_key=KEY, what=what, target=phone,
                            new_value=body, reason=reason[:300]))
    db.commit()


def _run(db: Session, payload: dict, now: datetime) -> None:
    if not enabled():
        return
    ev = _find(db, payload)
    if ev is None or not isinstance(ev.ai_call, dict):
        return
    phone = _phone(db, ev, payload)
    if not phone or phone in {fconf.ten(n) for n in (
            "+19544207373", "+17869200331", "+19549147244", "+18583794393")}:
        return
    later = next_send_time(now)
    if later is not None:
        job = enqueue(db, JOB_TYPE, payload, run_after=later,
                      dedupe_key=("dropped_call:%s:%s" % (payload.get("event_key"),
                                                          later.date()))[:200])
        if job is not None:
            job.max_attempts = 1
        db.commit()
        return
    call_at = dc.aware(ev.occurred_at)
    ok, why = decide(db, ev.ai_call, now)
    if not ok:
        _record(db, now, phone, "skipped", why if ok is not None else "not sent: " + why)
        if ok is None:
            registry.alert_failure(db, KEY, why, now)
        return
    for check, sentence in ((lambda: texted_today(db, phone, now), "already texted today"),
                            (lambda: opted_out_here(db, phone), "the customer texted STOP"),
                            (lambda: person_reached_them(db, phone, call_at),
                             "a person already called or texted them back")):
        if check():
            _record(db, now, phone, "skipped", sentence)
            return
    lv = registry.level(db, KEY, "send_text")
    if lv == rcat.NEVER:
        _record(db, now, phone, "skipped", "texting is at Never")
        return
    customer_uid, job_uid = zuper_customer(db, phone)
    line = "Zuper (954) 420-7373" if customer_uid else "CRM line"
    if lv == rcat.SHADOW:
        _record(db, now, phone, "shadow", "would text from %s: %s" % (line, why), TEXT)
        return
    _record(db, now, phone, "text", "sending from %s: %s" % (line, why), TEXT)  # BEFORE send
    try:
        if customer_uid:
            _send_zuper(customer_uid, phone, job_uid)
            result = "sent from the Zuper line"
        else:
            result = _send_crm(db, phone)
    except client.ZuperError as e:
        result = "Zuper refused: %s" % client.sentence(e)
        registry.alert_failure(db, KEY, result, now)
    db.add(AgentRegistryLog(at=now, entry_key=KEY, what="result", target=phone,
                            reason=str(result)[:300]))
    db.commit()


__all__ = ["TEXT", "decide", "handle_job", "maybe_enqueue", "pre_check"]
