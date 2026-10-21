"""One pass of the call follow-up, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"started", "texted", "called", "waiting", "done", "stopped"?}

Gates, in order - the first closed one stops the pass with a sentence:
  1. the server gate CALL_FOLLOWUP_ENABLED;
  2. "Pause all AI agents";
  3. the registry: both actions at Never = nothing to do; a spend limit reached.
Then: every team call that went unanswered in the last hours (Quo, the CRM line, Zuper Connect -
`dispatch.comms`) on a number in scope (only the test customers while "test only" is on) opens
ONE follow-up row; each open row moves by `rules.next_step`: text, wait, call, done.

Each action obeys its own level in the registry: Never skips it, Shadow records what it WOULD
do and does nothing, Automatic does it. A text or a call is recorded BEFORE it is sent and is
never retried - owen-main's call request is not idempotent and a retry could call twice.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import crmlink
from ..ai import engine as ai_engine
from ..dispatch import comms as dcomms
from ..models import AgentPermission, AgentRegistryEntry, CallFollowup
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import client
from ..zuper import config as zconfig
from . import config as c
from . import rules, zconnect

log = logging.getLogger("followup")

CALL_REASON = "your roof - we tried to reach you a few minutes ago"
OUR_CALL_WINDOW = timedelta(minutes=15)


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def since(db: Session) -> datetime | None:
    """When a level of this agent last changed: a promotion restarts the clock, so calls from
    before it are never acted on with the new freedom (no backlog)."""
    at = db.scalar(select(AgentPermission.changed_at).where(
        AgentPermission.entry_key == c.KEY).order_by(AgentPermission.changed_at.desc()))
    return c.aware(at) if at else None


def closed_gate(db: Session, now: datetime) -> str | None:
    if not zconfig.followup_enabled():
        return zconfig.FOLLOWUP_OFF_SENTENCE
    if ai_engine.settings(db).paused:
        return "All AI agents are paused (Settings -> AI Connections)."
    text_lv = registry.level(db, c.KEY, "send_text")
    call_lv = registry.level(db, c.KEY, "place_calls")
    if text_lv == rcat.NEVER and call_lv == rcat.NEVER:
        return "Both of its actions are at Never (AI Agents -> Overview)."
    over = registry.cap_refusal(db, c.KEY, now)
    if over:
        registry.alert_failure(db, c.KEY, over, now, what="cap_reached")
        return over
    return None


def _call(x: dcomms.Comm) -> rules.Call:
    return rules.Call(at=x.at, kind=x.kind, out=x.out, answered=x.answered, ai=x.ai,
                      final=x.final, source=x.source, staff=x.staff)


def _day_start(now: datetime) -> datetime:
    return c.local(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _ours(rows: list[CallFollowup], t: rules.Call) -> bool:
    """Our own AI call reported back as an unanswered outgoing call - never a trigger. Matched
    by time, whatever line reports it: owen-main's report may not carry the AI mark."""
    return any(
        r.call_at and timedelta(minutes=-1) <= t.at - c.aware(r.call_at) <= OUR_CALL_WINDOW
        for r in rows)


def _open(db: Session, phone: str, t: rules.Call, cust: zconnect.Customer) -> CallFollowup | None:
    key = "%s|%s|%s" % (phone, t.source, t.at.astimezone(UTC).isoformat())
    row = CallFollowup(trigger_key=key, phone=phone, trigger_at=t.at, trigger_source=t.source,
                       staff=t.staff, customer_uid=cust.customer_uid, customer_name=cust.name,
                       job_uid=cust.job_uid, job_number=cust.job_number, state="open",
                       reason="our call was not answered")
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    db.commit()
    return row


def _note(row: CallFollowup, text: str) -> None:
    if not row.job_uid:
        return
    try:
        zconnect.add_note(row.job_uid, text)
    except client.ZuperError as e:
        row.error = ("note: %s" % client.sentence(e))[:500]


def _text(db: Session, row: CallFollowup, s: c.Settings, now: datetime) -> str:
    lv = registry.level(db, c.KEY, "send_text")
    row.text_at = now
    if lv == rcat.NEVER:
        row.text_state, row.reason = "skipped", "texting is at Never"
        return "skipped"
    if not row.customer_uid:
        row.text_state, row.reason = "skipped", "not a Zuper customer, so Zuper cannot text them"
        return "skipped"
    first = (row.customer_name or "").split(" ")[0] if row.customer_name else None
    row.text_body = rules.render(s.text_template, first, c.DEFAULT_TEXT_NO_NAME)
    if lv == rcat.SHADOW:
        row.text_state, row.reason = "shadow", "would have texted (shadow)"
        return "shadow"
    problem = None
    try:
        problem = zconnect.sender_check()
    except client.ZuperError as e:
        problem = "could not read Zuper's sender number: %s" % client.sentence(e)
    if problem:
        row.text_at = None                     # nothing sent: try again next pass
        row.reason = problem
        return "blocked"
    row.text_state = "sending"
    db.commit()                                # recorded BEFORE the send - never twice
    try:
        zconnect.send(row.customer_uid, row.phone, row.text_body)
    except client.ZuperError as e:
        row.text_state, row.error = "failed", client.sentence(e)[:500]
        registry.alert_failure(db, c.KEY, "Zuper did not send a text: %s" % row.error, now)
        return "failed"
    row.text_state, row.reason = "sent", "texted"
    _note(row, "Call follow-up: our call at %s was not answered, so this text was sent from "
               "%s:\n\"%s\"" % (c.local(row.trigger_at).strftime("%I:%M %p"),
                                "(954) 420-7373", row.text_body))
    return "sent"


def _place_call(db: Session, row: CallFollowup, s: c.Settings, now: datetime) -> str:
    lv = registry.level(db, c.KEY, "place_calls")
    row.call_at = now
    row.state = "done"
    if lv == rcat.NEVER:
        row.call_state, row.reason = "skipped", "calling is at Never"
        return "skipped"
    if lv == rcat.SHADOW:
        row.call_state, row.reason = "shadow", "would have had Anisa call (shadow)"
        return "shadow"
    if not crmlink.configured():
        row.call_state, row.reason = "skipped", "the phone system link is not configured"
        return "skipped"
    row.call_state = "requested"
    db.commit()                                # recorded BEFORE the request - never twice
    first = (row.customer_name or "").split(" ")[0] if row.customer_name else None
    r = crmlink.agent_call("+1" + row.phone, s.call_agent, CALL_REASON, from_number=s.caller_id,
                           first_name=first if first and first.isalpha() else None,
                           requested_by="CRM call follow-up")
    if not r.ok:
        row.call_state, row.error = "failed", r.reason[:500]
        registry.alert_failure(db, c.KEY, "The phone system did not place Anisa's call: %s"
                               % r.reason, now)
        return "failed"
    row.call_ref = str((r.data or {}).get("call_id") or "")[:64] or None
    row.reason = "Anisa is calling"
    _note(row, "Call follow-up: no answer to our call or our text, so Anisa (AI) is calling "
               "the customer now from %s." % s.caller_id)
    return "called"


def run(db: Session, now: datetime | None = None) -> dict:
    now = c.aware(now or datetime.now(UTC))
    out = {"started": 0, "texted": 0, "called": 0, "waiting": 0, "done": 0}
    gate = closed_gate(db, now)
    if gate:
        db.commit()
        return dict(out, stopped=gate)
    s = settings(db)
    lookback = timedelta(hours=c.LOOKBACK_HOURS)
    talk = dcomms.load(db, now - lookback - timedelta(hours=1))
    in_scope = zconnect.test_phones(db, s) if s.test_only else None
    start = since(db)
    open_rows = {r.phone: r for r in db.scalars(select(CallFollowup).where(
        CallFollowup.state == "open"))}
    for phone, items in talk.items():
        if in_scope is not None and phone not in in_scope:
            continue
        if phone in open_rows:
            continue
        calls = [_call(x) for x in items]
        t = rules.trigger(calls, now=now, since=start, lookback=lookback)
        if t is None:
            continue
        mine = list(db.scalars(select(CallFollowup).where(CallFollowup.phone == phone)))
        if _ours(mine, t) or any(c.aware(m.trigger_at) >= t.at for m in mine):
            continue
        row = _open(db, phone, t, zconnect.customer_for(db, phone, s))
        if row is not None:
            open_rows[phone] = row
            out["started"] += 1
    day = _day_start(now)
    for phone, row in open_rows.items():
        if in_scope is not None and phone not in in_scope:
            row.state, row.reason = "skipped", "no longer a test number"
            db.commit()
            continue
        trig = c.aware(row.trigger_at)
        after = [_call(x) for x in talk.get(phone, []) if x.at > trig]
        calls_today = sum(1 for m in db.scalars(select(CallFollowup).where(
            CallFollowup.phone == phone, CallFollowup.call_at >= day,
            CallFollowup.call_state.in_(("requested", "shadow")))))
        step = rules.next_step(
            trigger_at=trig, text_at=c.aware(row.text_at) if row.text_at else None,
            called=row.call_state is not None, after=after, now=now,
            text_delay=timedelta(minutes=s.text_delay_minutes),
            wait=timedelta(minutes=s.wait_minutes), lookback=lookback,
            in_call_hours=c.call_hours(now), calls_today=calls_today,
            calls_per_day=c.CALLS_PER_DAY)
        row.updated_at = now
        try:
            if step.action == "text":
                if _text(db, row, s, now) in ("sent", "shadow"):
                    out["texted"] += 1
            elif step.action == "call":
                if _place_call(db, row, s, now) in ("called", "shadow"):
                    out["called"] += 1
            elif step.action == "wait":
                row.reason = step.reason
                out["waiting"] += 1
            else:
                row.state = "done" if step.action == "done" else "expired"
                row.reason = step.reason
                out["done"] += 1
        except Exception:
            db.rollback()
            log.exception("call follow-up failed on row %s", row.id)
            continue
        db.commit()
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.followup_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("call follow-up pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="call-followup", daemon=True)
    t.start()
    return t
