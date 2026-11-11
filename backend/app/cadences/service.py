"""One pass of Follow-ups, every POLL_SECONDS on its own worker thread, and the changes a person
makes on the page (start, pause, resume, skip, stop, reassign, restart).

    run(db, now) -> {"started", "stopped", "texted", "called", "due", "closed", "halted"?}

Gate: the server's FOLLOWUPS_ENABLED (closed while the old CALL_FOLLOWUP_ENABLED is also set)
and the Zuper sync's key. Then, every pass:

  1. every person's call that nobody picked up in the last hours (Quo, the CRM line, Zuper
     Connect - `dispatch.comms`) on a number whose Zuper customer has an open job starts ONE
     follow-up with that board's default sequence (test customers only while test-only is on);
  2. each open follow-up reads what happened since it started - those calls, plus the Connect
     texts on the 954 line - writes each new thing on its timeline once, and STOPS when the
     customer texted or called back or somebody reached them;
  3. a step that is due runs: a text (Zuper, from 954-420-7373), an AI call (Anisa through
     owen-main) or a "call me" reminder to the assignee, who is rung once and waited for.

A text and an AI call are re-checked right before they leave (a fresh Connect read), obey the
registry level (Never skips the step, Shadow records it and sends nothing, Automatic sends), the
hours, the caps and the 24-hour rule, and are RECORDED before they are sent, never retried.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import crmlink
from ..ai import engine as ai_engine
from ..dispatch import alerts as dalerts
from ..dispatch import comms as dcomms
from ..models import (
    AgentPermission,
    AgentRegistryEntry,
    AiAlert,
    AutoReply,
    CallFollowup,
    Contact,
    DispatchJob,
    Followup,
    FollowupEvent,
    FollowupSequence,
    User,
)
from ..phone_match import phone_clause
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import client
from ..zuper import config as zconfig
from . import config as c
from . import rules, zconnect

log = logging.getLogger("followups")

OPEN_STATES = ("open", "paused")
SENT = ("sending", "sent")
AI_CALL_REASON = "your roof - we have been trying to reach you"


class Refused(Exception):
    """A person's change that cannot be made; the sentence says why."""


# ---- settings, gate ---------------------------------------------------------------------------

def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def since(db: Session) -> datetime | None:
    """When a level of this entry last changed: a promotion restarts the clock, so calls from
    before it never start a follow-up with the new freedom (no backlog)."""
    at = db.scalar(select(AgentPermission.changed_at).where(
        AgentPermission.entry_key == c.KEY).order_by(AgentPermission.changed_at.desc()))
    return c.aware(at) if at else None


def closed_gate() -> str | None:
    if zconfig.followups_clash():
        return zconfig.FOLLOWUPS_CLASH_SENTENCE
    if not zconfig.followups_enabled():
        return zconfig.FOLLOWUPS_OFF_SENTENCE
    if not (zconfig.env_enabled() and zconfig.key_set()):
        return "The Zuper sync is not on, so Follow-ups cannot read Zuper Connect."
    return None


def test_phones(db: Session, s: c.Settings) -> set[str]:
    out = set(s.test_phones)
    for phones in db.scalars(select(DispatchJob.phones).where(
            DispatchJob.customer_uid.in_(c.TEST_CUSTOMERS))):
        out |= {c.ten(p) for p in phones or [] if c.ten(p)}
    return out


# ---- sequences --------------------------------------------------------------------------------

def ensure_sequences(db: Session) -> None:
    """The default sequences (Q21), created once. Never overwrites what an admin changed."""
    if db.scalar(select(func.count()).select_from(FollowupSequence)):
        return
    for d in c.DEFAULT_SEQUENCES:
        db.add(FollowupSequence(name=d["name"], board=d["board"], is_default=True,
                                steps=d["steps"]))
    db.flush()


def sequence_for(db: Session, board: str | None) -> FollowupSequence | None:
    ensure_sequences(db)
    rows = list(db.scalars(select(FollowupSequence).where(
        FollowupSequence.active.is_(True), FollowupSequence.is_default.is_(True))
        .order_by(FollowupSequence.id)))
    return (next((r for r in rows if board and r.board == board), None)
            or next((r for r in rows if r.board is None), None)
            or (rows[0] if rows else None))


# ---- who the number belongs to -----------------------------------------------------------------

def customer_for(db: Session, phone: str) -> dict:
    """The Zuper customer and job a number belongs to, from the Dispatch reader's copy. A number
    shared by two Zuper customers is nobody's. `open_job` = a job not cancelled, paid or lost."""
    jobs = [j for j in db.scalars(select(DispatchJob).where(DispatchJob.customer_uid.isnot(None)))
            if phone in {c.ten(p) for p in j.phones or []}]
    out = {"customer_uid": None, "customer_name": None, "job_uid": None, "job_number": None,
           "board": None, "open_job": False}
    if len({j.customer_uid for j in jobs}) != 1:
        return out

    def live(j: DispatchJob) -> bool:
        return bool(j.is_open) and (j.status_type or "").upper() not in c.CLOSED_STATUS_TYPES

    best = min(jobs, key=lambda j: (not live(j), -(j.id or 0)))
    out.update(customer_uid=best.customer_uid, customer_name=best.customer_name,
               job_uid=best.job_uid, job_number=best.job_number, board=best.board,
               open_job=live(best))
    return out


def contact_for(db: Session, phone: str) -> Contact | None:
    rows = list(db.scalars(select(Contact).where(phone_clause(Contact.phone, phone)).limit(3)))
    return rows[0] if len(rows) == 1 else None


def user_for_staff(db: Session, staff: str | None) -> User | None:
    """The CRM user a phone line's "who called" names: the full name, else a unique first name."""
    if not staff:
        return None
    name = staff.strip().casefold()
    users = [u for u in db.scalars(select(User).where(User.is_active.is_(True)))
             if u.name and (u.role.value if hasattr(u.role, "value") else u.role) != "TECH"]
    exact = [u for u in users if u.name.strip().casefold() == name]
    if len(exact) == 1:
        return exact[0]
    first = [u for u in users if u.name.strip().split(" ")[0].casefold() == name.split(" ")[0]]
    return first[0] if len(first) == 1 else None


# ---- the timeline ------------------------------------------------------------------------------

def add_event(db: Session, row: Followup, kind: str, at: datetime, text: str, *,
              key: str | None = None, state: str | None = None, step: int | None = None,
              who: str | None = None) -> FollowupEvent | None:
    """Write one timeline line. With a `key`, a second write of the same thing is dropped."""
    ev = FollowupEvent(followup_id=row.id, key=("%d|%s" % (row.id, key))[:200] if key else None,
                       at=at, kind=kind, state=state, step=step, text=text, who=who)
    if key is None:
        db.add(ev)
        db.flush()
        return ev
    try:
        with db.begin_nested():
            db.add(ev)
            db.flush()
    except IntegrityError:
        return None
    return ev


def events(db: Session, row: Followup) -> list[FollowupEvent]:
    return list(db.scalars(select(FollowupEvent).where(FollowupEvent.followup_id == row.id)
                           .order_by(FollowupEvent.at, FollowupEvent.id)))


def _alert(db: Session, row: Followup, kind: str, title: str, body: str, *,
           urgent: bool = False) -> None:
    """The assignee's bell (and desktop alert); nobody assigned = everyone who works Dispatch."""
    people: list[int]
    if row.assigned_user_id:
        people = [row.assigned_user_id]
    else:
        people = []
        for u in dalerts.recipients(db):
            if row.board and row.board in dalerts.hidden_boards(db, u.id, u.role):
                continue
            people.append(u.id)
    opp_id, contact_id = dalerts.card_for(db, row.job_uid)
    for uid in people:
        db.add(AiAlert(user_id=uid, kind=kind[:30], title=title[:300], body=body, urgent=urgent,
                       opportunity_id=opp_id, contact_id=contact_id or row.contact_id))


def who(row: Followup) -> str:
    if row.customer_name:
        return row.customer_name
    p = row.phone
    return "(%s) %s-%s" % (p[:3], p[3:6], p[6:])


# ---- opening -----------------------------------------------------------------------------------

def open_for(db: Session, phone: str) -> Followup | None:
    return db.scalar(select(Followup).where(Followup.phone == phone,
                                            Followup.state.in_(OPEN_STATES)))


def start(db: Session, phone: str, now: datetime, *, source: str, sequence: FollowupSequence,
          start_at: datetime | None = None, note: str | None = None,
          assigned: User | None = None, assigned_label: str | None = None,
          trigger_key: str | None = None, created_by: str | None = None,
          skip_first_text: bool = False) -> Followup | None:
    """Open a follow-up; None if this number already has one open or this call already started
    one. The first step is timed from `start_at` (a manual "call me Friday") or from now."""
    if open_for(db, phone) is not None:
        return None
    cust = customer_for(db, phone)
    contact = contact_for(db, phone)
    steps = list(sequence.steps or [])
    begin = c.aware(start_at) if start_at else now
    row = Followup(phone=phone, state="open", source=source, trigger_key=trigger_key,
                   customer_uid=cust["customer_uid"],
                   customer_name=cust["customer_name"] or (
                       " ".join(x for x in (contact.first_name, contact.last_name) if x)
                       if contact else None) or None,
                   job_uid=cust["job_uid"], job_number=cust["job_number"], board=cust["board"],
                   contact_id=contact.id if contact else None, sequence_id=sequence.id,
                   sequence_name=sequence.name, steps=steps, step_index=0,
                   next_at=rules.due_at(steps[0], begin) if steps else None,
                   assigned_user_id=assigned.id if assigned else None,
                   assigned_label=(assigned.name if assigned else assigned_label),
                   note=note, started_at=now, created_by=created_by, updated_at=now)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    add_event(db, row, "started", now, "Started (%s) with the %s sequence%s." % (
        "after an unanswered call" if source == "auto" else "added by %s" % (
            created_by or "a person"), sequence.name,
        "; first step %s" % c.local(row.next_at).strftime("%a %b %d, %I:%M %p")
        if row.next_at else ""), who=created_by)
    if skip_first_text and steps and steps[0].get("type") == "text":
        add_event(db, row, "step", now, "Step 1 (text) skipped: this number was texted "
                  "automatically in the last 24 hours.", state="skipped", step=0)
        _advance(row, now)
    return row


# ---- what happened -----------------------------------------------------------------------------

def _ev(x: dcomms.Comm) -> rules.Ev:
    return rules.Ev(uid="%s:%s:%s" % (x.source, x.kind, x.at.astimezone(UTC).isoformat()),
                    at=x.at, kind=x.kind, out=x.out, answered=x.answered, ai=x.ai,
                    final=x.final, source=x.source, staff=x.staff, body=x.summary or "")


def _ai_call_times(db: Session, phone: str) -> list[datetime]:
    rows = db.execute(select(FollowupEvent.at).join(Followup, Followup.id ==
                                                    FollowupEvent.followup_id).where(
        Followup.phone == phone, FollowupEvent.kind == "ai_call",
        FollowupEvent.state.in_(("requested", "shadow")))).all()
    return [c.aware(r[0]) for r in rows]


def _our_texts(db: Session, row: Followup) -> set[str]:
    return {(t or "").strip() for t in db.scalars(select(FollowupEvent.text).where(
        FollowupEvent.followup_id == row.id, FollowupEvent.kind == "text_sent"))}


def auto_texted_recently(db: Session, phone: str, now: datetime) -> bool:
    """Q26: any module's automatic text to this number in the last 24 hours."""
    floor = now - timedelta(hours=c.AUTO_TEXT_GAP_HOURS)
    if db.scalar(select(func.count()).select_from(FollowupEvent).join(
            Followup, Followup.id == FollowupEvent.followup_id).where(
            Followup.phone == phone, FollowupEvent.kind == "text",
            FollowupEvent.state.in_(SENT), FollowupEvent.at >= floor)):
        return True
    if db.scalar(select(func.count()).select_from(AutoReply).where(
            AutoReply.phone == phone, AutoReply.state.in_(SENT),
            or_(AutoReply.sent_at >= floor, AutoReply.created_at >= floor))):
        return True
    return bool(db.scalar(select(func.count()).select_from(CallFollowup).where(
        CallFollowup.phone == phone, CallFollowup.text_state.in_(SENT),
        CallFollowup.text_at >= floor)))


def _step_began(db: Session, row: Followup) -> datetime:
    """When the current step became the current one: the last step line, else the start."""
    at = db.scalar(select(func.max(FollowupEvent.at)).where(
        FollowupEvent.followup_id == row.id, FollowupEvent.kind == "step"))
    return c.aware(at) if at else c.aware(row.started_at)


def stop(db: Session, row: Followup, now: datetime, reason: str, *, state: str = "stopped",
         by: str | None = None) -> None:
    row.state, row.stop_reason, row.closed_at, row.next_at = state, reason[:500], now, None
    row.updated_at = now
    add_event(db, row, "stopped" if state == "stopped" else "closed", now, reason, who=by)


def observe(db: Session, row: Followup, evs: list[rules.Ev], now: datetime) -> str | None:
    """Write what happened since the start on the timeline (once each); stop on a reply, a call
    back or a reached customer. Returns why it stopped, or None."""
    started = c.aware(row.started_at)
    ai_times = _ai_call_times(db, row.phone)
    ours_texts = _our_texts(db, row)
    began = _step_began(db, row)
    current = row.steps[row.step_index] if row.step_index < len(row.steps or []) else None
    for e in sorted(evs, key=lambda x: x.at):
        if e.at <= started:
            continue
        ours = (rules.ours_call(e, ai_times) if e.kind == "call"
                else e.out and e.body.strip() in ours_texts)
        m = rules.meaning(e, ours=ours)
        if m.what == rules.IGNORE:
            continue
        written = add_event(db, row, m.kind, e.at, m.reason + (
            ": \u201c%s\u201d" % e.body[:300] if e.kind == "text" and e.body else ""),
            key=e.uid, who=e.staff)
        if m.what == rules.STOP:
            if row.state in OPEN_STATES:
                stop(db, row, now, m.reason)
                kind = "followup_reply" if m.kind in ("reply", "call_back") else "followup_reached"
                if m.kind in ("reply", "call_back"):
                    _alert(db, row, kind, "%s: %s" % (who(row), m.reason.lower()),
                           "Follow-up stopped. %s." % m.reason, urgent=True)
            return m.reason
        if written is not None and m.person_call and current \
                and current.get("type") == "call_me" and e.at >= began - timedelta(hours=2):
            add_event(db, row, "step", e.at, "Step %d (call me) done: %s." % (
                row.step_index + 1, m.reason.lower()), state="done", step=row.step_index)
            _advance(row, e.at)
            current = row.steps[row.step_index] if row.step_index < len(row.steps) else None
            began = e.at
    return None


# ---- steps -------------------------------------------------------------------------------------

def _advance(row: Followup, done_at: datetime) -> None:
    row.step_index += 1
    row.due_alerted_step = None
    if row.step_index < len(row.steps or []):
        row.next_at = rules.due_at(row.steps[row.step_index], max(done_at, c.aware(
            row.next_at) if row.next_at else done_at))
    else:
        row.next_at = c.aware(done_at) + timedelta(hours=c.GRACE_HOURS)


def _count_today(db: Session, kind: str, states: tuple[str, ...], now: datetime,
                 phone: str | None = None) -> int:
    day = c.local(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    q = select(func.count()).select_from(FollowupEvent).join(
        Followup, Followup.id == FollowupEvent.followup_id).where(
        FollowupEvent.kind == kind, FollowupEvent.state.in_(states), FollowupEvent.at >= day)
    if phone:
        q = q.where(Followup.phone == phone)
    return db.scalar(q) or 0


def _tomorrow(now: datetime) -> datetime:
    t = c.local(now) + timedelta(days=1)
    return rules.next_open(t.replace(hour=0, minute=0, second=0, microsecond=0))


def _blocked(db: Session, row: Followup, conv: dict | None) -> str | None:
    contact = db.get(Contact, row.contact_id) if row.contact_id else None
    if contact is not None and contact.dnd:
        return "the contact is marked Do Not Disturb"
    if conv and conv.get("blocked"):
        return "the number is blocked in Zuper Connect"
    return None


def _skip(db: Session, row: Followup, now: datetime, kind: str, why: str) -> str:
    add_event(db, row, kind, now, "Step %d skipped: %s." % (row.step_index + 1, why),
              state="skipped", step=row.step_index)
    add_event(db, row, "step", now, "Step %d skipped." % (row.step_index + 1), state="skipped",
              step=row.step_index)
    _advance(row, now)
    return "skipped"


def _fresh_check(db: Session, row: Followup, talk: list[rules.Ev], now: datetime
                 ) -> tuple[str | None, dict | None]:
    """Right before a text or a call leaves: read the 954 conversation again. (why stopped,
    the conversation)."""
    conv = zconnect.find(row.phone)
    evs = list(talk)
    if conv:
        evs += zconnect.texts(conv["uid"], c.aware(row.started_at))
        row.connect_seen_at = conv.get("recent_at") or now
    return observe(db, row, evs, now), conv


def _note(row: Followup, text: str) -> str | None:
    if not row.job_uid:
        return None
    try:
        zconnect.add_note(row.job_uid, text)
    except client.ZuperError as e:
        return client.sentence(e)
    return None


def _text(db: Session, row: Followup, step: dict, s: c.Settings, talk: list[rules.Ev],
          now: datetime) -> str:
    if not rules.sending_hours(now):
        row.next_at = rules.next_open(now)
        return "wait"
    if s.test_only and row.phone not in test_phones(db, s):
        return _skip(db, row, now, "text", "test customers only - this number is not one")
    lv = registry.level(db, c.KEY, "send_text")
    if lv == rcat.NEVER:
        return _skip(db, row, now, "text", "texting is at Never (AI Agents -> Overview)")
    if not row.customer_uid:
        return _skip(db, row, now, "text", "not a Zuper customer, so Zuper cannot text them")
    if auto_texted_recently(db, row.phone, now):
        return _skip(db, row, now, "text", "this number was texted automatically in the "
                     "last 24 hours")
    if _count_today(db, "text", (*SENT, "shadow"), now) >= s.texts_per_day:
        row.next_at = _tomorrow(now)
        add_event(db, row, "waiting", now, "Today's limit of %d follow-up texts is used up; "
                  "trying again tomorrow." % s.texts_per_day,
                  key="cap-text-%s" % c.local(now).date())
        return "wait"
    over = registry.cap_refusal(db, c.KEY, now)
    if over:
        row.next_at = _tomorrow(now)
        return "wait"
    body = rules.render(step.get("template") or "", rules.first_name(row.customer_name))
    if lv == rcat.SHADOW:
        add_event(db, row, "text", now, "Would have texted (shadow): \u201c%s\u201d" % body,
                  state="shadow", step=row.step_index)
        add_event(db, row, "step", now, "Step %d (text) done in shadow." % (
            row.step_index + 1), state="shadow", step=row.step_index)
        _advance(row, now)
        return "shadow_text"
    why, conv = _fresh_check(db, row, talk, now)
    if why:
        return "stopped"
    blocked = _blocked(db, row, conv)
    if blocked:
        return _skip(db, row, now, "text", blocked)
    try:
        problem = zconnect.sender_check()
    except client.ZuperError as e:
        problem = "could not read Zuper's sender number: %s" % client.sentence(e)
    if problem:
        add_event(db, row, "waiting", now, "Not texted yet: %s." % problem,
                  key="sender-%s" % c.local(now).strftime("%Y-%m-%d %H"))
        return "wait"
    ev = add_event(db, row, "text", now, body, state="sending", step=row.step_index)
    step_no = row.step_index + 1
    _advance(row, now)            # with the record, BEFORE the send: a crash never sends twice
    db.commit()
    try:
        zconnect.send(row.customer_uid, row.phone, body)
    except client.ZuperError as e:
        ev.state = "failed"
        ev.text = "Could not text: %s - \u201c%s\u201d" % (client.sentence(e), body)
        _alert(db, row, "followup_failed", "Follow-up text to %s failed" % who(row),
               client.sentence(e))
        registry.alert_failure(db, c.KEY, "Zuper did not send a follow-up text: %s"
                               % client.sentence(e), now)
        add_event(db, row, "step", now, "Step %d (text) failed; moving on." % step_no,
                  state="failed", step=step_no - 1)
        return "failed"
    ev.state = "sent"
    add_event(db, row, "text_sent", now, body)    # how our own text is recognised in Connect
    add_event(db, row, "step", now, "Step %d (text) sent." % step_no, state="done",
              step=step_no - 1)
    problem = _note(row, "Follow-up: the customer did not answer, so this text was sent from "
                         "%s:\n\"%s\"" % (c.SENDER_DISPLAY if hasattr(c, "SENDER_DISPLAY")
                                          else "(954) 420-7373", body))
    if problem:
        add_event(db, row, "note", now, "The Zuper note was not added: %s" % problem)
    return "sent"


def _ai_call(db: Session, row: Followup, s: c.Settings, talk: list[rules.Ev],
             now: datetime) -> str:
    if not rules.sending_hours(now):
        row.next_at = rules.next_open(now)
        return "wait"
    if s.test_only and row.phone not in test_phones(db, s):
        return _skip(db, row, now, "ai_call", "test customers only - this number is not one")
    lv = registry.level(db, c.KEY, "place_calls")
    if lv == rcat.NEVER:
        return _skip(db, row, now, "ai_call", "AI calls are at Never (AI Agents -> Overview)")
    if ai_engine.settings(db).paused:
        add_event(db, row, "waiting", now, "Waiting: all AI agents are paused.",
                  key="paused-%s" % c.local(now).date())
        return "wait"
    if _count_today(db, "ai_call", ("requested", "shadow"), now, row.phone) \
            >= c.AI_CALLS_PER_NUMBER_DAY:
        row.next_at = _tomorrow(now)
        return "wait"
    if _count_today(db, "ai_call", ("requested", "shadow"), now) >= s.ai_calls_per_day:
        row.next_at = _tomorrow(now)
        add_event(db, row, "waiting", now, "Today's limit of %d AI calls is used up; trying "
                  "again tomorrow." % s.ai_calls_per_day, key="cap-call-%s" % c.local(now).date())
        return "wait"
    if lv == rcat.SHADOW:
        add_event(db, row, "ai_call", now, "Would have had Anisa call (shadow).",
                  state="shadow", step=row.step_index)
        add_event(db, row, "step", now, "Step %d (AI call) done in shadow." % (
            row.step_index + 1), state="shadow", step=row.step_index)
        _advance(row, now)
        return "shadow_call"
    why, conv = _fresh_check(db, row, talk, now)
    if why:
        return "stopped"
    blocked = _blocked(db, row, conv)
    if blocked:
        return _skip(db, row, now, "ai_call", blocked)
    if not crmlink.configured():
        return _skip(db, row, now, "ai_call", "the phone system link is not configured")
    caller = s.caller_id or crmlink.current().from_number
    ev = add_event(db, row, "ai_call", now, "Anisa (AI) is calling from %s." % caller,
                   state="requested", step=row.step_index)
    step_no = row.step_index + 1
    _advance(row, now)            # with the record, BEFORE the request: never two calls
    db.commit()
    r = crmlink.agent_call("+1" + row.phone, s.call_agent, AI_CALL_REASON, from_number=caller,
                           first_name=rules.first_name(row.customer_name),
                           requested_by="CRM follow-ups")
    if not r.ok:
        ev.state = "failed"
        ev.text = "The phone system did not place Anisa's call: %s" % r.reason
        _alert(db, row, "followup_failed", "Follow-up AI call to %s failed" % who(row),
               r.reason)
        registry.alert_failure(db, c.KEY, "The phone system did not place a follow-up AI "
                               "call: %s" % r.reason, now)
        add_event(db, row, "step", now, "Step %d (AI call) failed; moving on." % step_no,
                  state="failed", step=step_no - 1)
        return "failed"
    add_event(db, row, "step", now, "Step %d (AI call) placed." % step_no, state="done",
              step=step_no - 1)
    problem = _note(row, "Follow-up: no answer so far, so Anisa (AI) is calling the customer "
                         "now from %s." % caller)
    if problem:
        add_event(db, row, "note", now, "The Zuper note was not added: %s" % problem)
    return "called"


def _call_me(db: Session, row: Followup, now: datetime) -> str:
    """Ring the assignee once; the step waits until they call (seen on any line) or skip it."""
    if row.due_alerted_step == row.step_index:
        return "due"
    row.due_alerted_step = row.step_index
    add_event(db, row, "call_due", now, "Step %d: time to call %s%s." % (
        row.step_index + 1, who(row), " (%s)" % row.assigned_label if row.assigned_label else ""),
        step=row.step_index)
    _alert(db, row, "followup_call_due", "Call %s now (follow-up)" % who(row),
           "Step %d of %s. Nobody has reached them since %s." % (
               row.step_index + 1, row.sequence_name or "the follow-up",
               c.local(row.started_at).strftime("%a %b %d")))
    return "due"


def run_step(db: Session, row: Followup, s: c.Settings, talk: list[rules.Ev],
             now: datetime) -> str:
    if row.step_index >= len(row.steps or []):
        stop(db, row, now, "No response after every step of %s." % (
            row.sequence_name or "the sequence"), state="no_response")
        return "closed"
    step = row.steps[row.step_index]
    kind = step.get("type")
    if kind == "text":
        return _text(db, row, step, s, talk, now)
    if kind == "ai_call":
        return _ai_call(db, row, s, talk, now)
    return _call_me(db, row, now)


# ---- the pass ----------------------------------------------------------------------------------

def run(db: Session, now: datetime | None = None) -> dict:
    now = c.aware(now or datetime.now(UTC))
    out = {"started": 0, "stopped": 0, "texted": 0, "called": 0, "due": 0, "closed": 0}
    gate = closed_gate()
    if gate:
        return dict(out, halted=gate)
    s = settings(db)
    ensure_sequences(db)
    rows = list(db.scalars(select(Followup).where(Followup.state.in_(OPEN_STATES))))
    floor = min([c.aware(r.started_at) for r in rows]
                + [now - timedelta(hours=c.LOOKBACK_HOURS)]) - timedelta(hours=1)
    talk = {p: [_ev(x) for x in items] for p, items in dcomms.load(db, floor).items()}
    scope = test_phones(db, s) if s.test_only else None

    # 1. unanswered calls start follow-ups
    began = since(db)
    open_phones = {r.phone for r in rows}
    for phone, evs in talk.items():
        if phone in open_phones or (scope is not None and phone not in scope):
            continue
        t = rules.trigger(evs, now=now, floor=began, ai_call_times=_ai_call_times(db, phone))
        if t is None:
            continue
        key = "%s|%s|%s" % (phone, t.source, t.at.astimezone(UTC).isoformat())
        if db.scalar(select(Followup.id).where(or_(
                Followup.trigger_key == key,
                (Followup.phone == phone) & (Followup.started_at >= t.at)))):
            continue
        cust = customer_for(db, phone)
        if not cust["open_job"]:
            continue
        seq = sequence_for(db, cust["board"])
        if seq is None:
            continue
        row = start(db, phone, now, source="auto", sequence=seq, trigger_key=key,
                    assigned=user_for_staff(db, t.staff), assigned_label=t.staff,
                    skip_first_text=auto_texted_recently(db, phone, now))
        if row is not None:
            row.started_at = t.at
            add_event(db, row, "team_call", t.at, "%s called - no answer" % (
                t.staff or "The team"), key=t.uid, who=t.staff)
            db.commit()
            rows.append(row)
            open_phones.add(phone)
            out["started"] += 1

    # 2. read the 954 conversations once
    box: dict[str, dict] = {}
    if rows:
        try:
            box = zconnect.inbox()
        except client.ZuperError as e:
            log.warning("follow-ups: could not read the Connect inbox: %s", client.sentence(e))

    # 3. each open follow-up
    for row in rows:
        try:
            evs = list(talk.get(row.phone, []))
            conv = box.get(row.phone)
            if conv and (row.connect_seen_at is None or (conv.get("recent_at") and c.aware(
                    conv["recent_at"]) > c.aware(row.connect_seen_at))):
                evs += zconnect.texts(conv["uid"], c.aware(row.started_at))
                row.connect_seen_at = conv.get("recent_at") or now
            if observe(db, row, evs, now):
                out["stopped"] += 1
                db.commit()
                continue
            if row.state != "open" or row.next_at is None or c.aware(row.next_at) > now:
                db.commit()
                continue
            r = run_step(db, row, s, evs, now)
            row.updated_at = now
            if r == "stopped":
                out["stopped"] += 1
            elif r in ("sent", "shadow_text"):
                out["texted"] += 1
            elif r in ("called", "shadow_call"):
                out["called"] += 1
            elif r == "due":
                out["due"] += 1
            elif r == "closed":
                out["closed"] += 1
            db.commit()
        except client.ZuperError as e:
            db.rollback()
            log.warning("follow-up %s: Zuper: %s", row.id, client.sentence(e))
        except Exception:
            db.rollback()
            log.exception("follow-up %s failed", row.id)
    return out


# ---- a person's changes ------------------------------------------------------------------------

def act(db: Session, row: Followup, action: str, now: datetime, *, by: str,
        reason: str | None = None, user: User | None = None,
        sequence: FollowupSequence | None = None, start_at: datetime | None = None) -> Followup:
    """pause | resume | skip | stop | reassign | restart."""
    if action == "pause":
        if row.state != "open":
            raise Refused("Only an open follow-up can be paused.")
        row.state = "paused"
        add_event(db, row, "paused", now, "Paused%s." % (": " + reason if reason else ""), who=by)
    elif action == "resume":
        if row.state != "paused":
            raise Refused("Only a paused follow-up can be resumed.")
        row.state = "open"
        if row.next_at is not None and c.aware(row.next_at) < now \
                and row.step_index < len(row.steps or []):
            row.next_at = rules.next_open(now)
        add_event(db, row, "resumed", now, "Resumed.", who=by)
    elif action == "skip":
        if row.state not in OPEN_STATES or row.step_index >= len(row.steps or []):
            raise Refused("There is no step left to skip.")
        add_event(db, row, "step", now, "Step %d (%s) skipped by %s%s." % (
            row.step_index + 1, c.STEP_LABEL.get(row.steps[row.step_index].get("type"), "?"),
            by, ": " + reason if reason else ""), state="skipped", step=row.step_index, who=by)
        _advance(row, now)
    elif action == "stop":
        if row.state not in OPEN_STATES:
            raise Refused("This follow-up is already closed.")
        if not (reason or "").strip():
            raise Refused("Say why it is stopped (for example: reached them, not interested).")
        stop(db, row, now, "Stopped by %s: %s" % (by, reason.strip()), by=by)
    elif action == "reassign":
        row.assigned_user_id = user.id if user else None
        row.assigned_label = user.name if user else None
        add_event(db, row, "reassigned", now, "Assigned to %s." % (
            user.name if user else "nobody (the whole office)"), who=by)
    elif action == "restart":
        if row.state in OPEN_STATES:
            raise Refused("It is still open.")
        if open_for(db, row.phone) is not None:
            raise Refused("This number already has an open follow-up.")
        seq = sequence or (db.get(FollowupSequence, row.sequence_id) if row.sequence_id
                           else None) or sequence_for(db, row.board)
        if seq is None:
            raise Refused("There is no sequence to restart it with.")
        new = start(db, row.phone, now, source="manual", sequence=seq, start_at=start_at,
                    note=row.note, created_by=by,
                    assigned=db.get(User, row.assigned_user_id) if row.assigned_user_id
                    else None)
        if new is None:
            raise Refused("It could not be restarted.")
        return new
    else:
        raise Refused("Unknown action.")
    row.updated_at = now
    return row


# ---- the worker thread -------------------------------------------------------------------------

def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.followups_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("follow-ups pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop_event: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop_event.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="followups", daemon=True)
    t.start()
    return t
