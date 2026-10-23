"""One pass of Reviews & QC, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"planned", "skipped", "asked", "replies", "low", "calls", "stopped"?}

Off unless REVIEWS_ENABLED. Then:
  1. PLAN: every job that reached Repair Complete after the agent started, `wait_days` later,
     becomes one `review_requests` row - planned, or skipped with the reason (unhappy words
     since the visit, an open issue, a callback job, money due, asked recently, test mode);
  2. ASK (registry `send_text`; Shadow by default; 8 AM-8 PM Mon-Sat): the 1-5 question,
     recorded before it is sent, a private note on the Zuper job;
  3. READ the customer's replies in Zuper Connect: a bare number in code, words by the AI
     (`ai_read`). 5 -> thank you + the review link. 1-4, or a leak -> "what could we do
     better?" + ONE Dispatch item in "Customer issues" + the bell; their answer is kept and the
     link offered too (Google's rule: never only to happy customers). Unclear -> a person reads it;
  4. NO REPLY after `no_reply_days`: Anisa calls (registry `place_calls`, Never by default);
     after GIVE_UP_DAYS the check closes as no_reply.
Never moves a Zuper column, never retries a send.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import crmlink
from .. import kpi_report as kr
from ..ai import engine as ai_engine
from ..ai import providers
from ..ai.providers import REASONING_ROOM
from ..autoreply import rules as arules
from ..dispatch import ai as dai
from ..dispatch import alerts
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..inspection_docs.rules import quote_ok
from ..models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    ConversationSignal,
    DispatchItem,
    DispatchJob,
    ReviewRequest,
    ZuperStatusHistory,
)
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import client
from ..zuper import config as zconfig
from . import config as c
from . import rules, zconnect

log = logging.getLogger("reviews")
KIND = "low_rating"
QUEUE = "issues"
CALL_REASON = ("Satisfaction check: we finished the customer's roof repair a few days ago and "
               "texted to ask how we did, with no answer. Ask how the work went from 1 to 5 and "
               "whether anything is still wrong. Never mention prices.")
READ_SYSTEM = (
    "A roofing company asked a customer to rate the finished repair from 1 to 5. Read the "
    "customer's reply. Answer JSON only: {\"score\": 1-5 or null if they gave none, \"leak\": "
    "true if they say it still leaks or there is water, \"quote\": the words that show it, copied "
    "exactly (5-25 words) or \"\"}. Do not guess a score from politeness alone.")


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def started_at(db: Session, now: datetime) -> datetime:
    at = db.scalar(select(AgentRegistryLog.at).where(
        AgentRegistryLog.entry_key == c.KEY, AgentRegistryLog.what == "started")
        .order_by(AgentRegistryLog.at))
    if at is not None:
        return dc.aware(at)
    db.add(AgentRegistryLog(at=now, entry_key=c.KEY, what="started",
                            new_value="first pass; jobs finished before this are not asked"))
    db.commit()
    return now


def _phone(j: DispatchJob) -> str | None:
    for p in j.phones or []:
        if c.ten(p):
            return c.ten(p)
    return None


def _note(row: ReviewRequest, text: str) -> None:
    try:
        zconnect.add_note(row.job_uid, "Reviews & QC: " + text)
    except client.ZuperError:
        log.warning("the note on job %s failed", row.job_number)


# ------------------------------------------------------------------------- 1. plan

def plan(db: Session, s: c.Settings, now: datetime, start: datetime, out: dict) -> None:
    done: dict[str, datetime] = {}
    for m in db.scalars(select(ZuperStatusHistory).where(
            ZuperStatusHistory.status_name == c.DONE_STATUS,
            ZuperStatusHistory.changed_at >= start)):
        at = dc.aware(m.changed_at)
        if m.job_uid not in done or at > done[m.job_uid]:
            done[m.job_uid] = at
    have = set(db.scalars(select(ReviewRequest.job_uid).where(
        ReviewRequest.job_uid.in_(list(done))))) if done else set()
    todo = {u: at for u, at in done.items() if u not in have
            and rules.due(at, now, s.wait_days, c.WINDOW_DAYS) != "early"}
    if not todo:
        return
    callbacks = {kr.parent_uid(j) for j in kr.latest_jobs(db) if kr.is_callback(j)}
    for uid, at in todo.items():
        j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == uid))
        phone = _phone(j) if j is not None else None
        row = ReviewRequest(job_uid=uid, job_number=j.job_number if j else None,
                            board=j.board if j else None,
                            customer_uid=j.customer_uid if j else None,
                            customer_name=j.customer_name if j else None, phone=phone,
                            technician=(j.technician or (j.assigned or [None])[0]) if j else None,
                            completed_at=at, state="planned")
        if rules.due(at, now, s.wait_days, c.WINDOW_DAYS) == "late":
            why = "finished more than %d days ago" % c.WINDOW_DAYS
        else:
            near = [ConversationSignal.job_uid == uid]
            items = [DispatchItem.job_uid == uid]
            if phone:
                near.append(ConversationSignal.phone == phone)
                items.append(DispatchItem.phone == phone)
            unhappy = db.scalar(select(ConversationSignal.tag).where(
                ConversationSignal.tag.in_(rules.UNHAPPY_TAGS), ConversationSignal.at >= at,
                or_(*near)).limit(1))
            open_kinds = db.scalars(select(DispatchItem.kind).where(
                DispatchItem.state == "open", or_(*items))).all()
            recent = bool(phone) and db.scalar(select(ReviewRequest.id).where(
                ReviewRequest.phone == phone, ReviewRequest.asked_at.isnot(None),
                ReviewRequest.asked_at >= now - timedelta(days=s.resurvey_days)).limit(1))
            why = rules.skip_reason(rules.Facts(
                completed_at=at, phone=phone, customer_uid=row.customer_uid,
                cancelled=bool(j and (j.status or "") == "Cancelled"), unhappy_tag=unhappy,
                open_issue=any(k in ("customer_issue", KIND) for k in open_kinds),
                callback_job=uid in callbacks, money_due="payment_due" in open_kinds,
                asked_recently=bool(recent),
                test=(row.customer_uid in c.TEST_CUSTOMERS) or (phone in s.test_phones)),
                test_only=s.test_only)
        if why:
            row.state, row.reason = "skipped", why
            out["skipped"] += 1
        else:
            out["planned"] += 1
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            continue
        db.commit()


# ------------------------------------------------------------------------- 2. ask

def ask(db: Session, s: c.Settings, now: datetime, out: dict, convs: dict | None) -> None:
    lv = registry.level(db, c.KEY, "send_text")
    rows = list(db.scalars(select(ReviewRequest).where(ReviewRequest.state == "planned")))
    for row in rows:
        if rules.due(dc.aware(row.completed_at), now, 0, c.WINDOW_DAYS) == "late":
            row.state, row.reason = "skipped", "finished more than %d days ago" % c.WINDOW_DAYS
            continue
    db.commit()
    if lv == rcat.NEVER or not c.call_hours(now):
        return
    sender_problem = None
    for row in [r for r in rows if r.state == "planned"]:
        body = rules.ask_text(row.customer_name)
        row.asked_at, row.asked_body = now, body
        if lv == rcat.SHADOW:
            row.asked_state, row.state = "shadow", "done"
            row.reason = "shadow: would have asked for a rating"
            db.commit()
            out["asked"] += 1
            continue
        if convs is not None and row.phone in convs and arules.opted_out(
                zconnect.messages(convs[row.phone])):
            row.state, row.reason = "skipped", "the customer texted STOP"
            db.commit()
            continue
        if sender_problem is None:
            sender_problem = zconnect.sender_check() or ""
        if sender_problem:
            row.asked_at = row.asked_body = None
            registry.alert_failure(db, c.KEY, "no text was sent: %s" % sender_problem, now)
            db.commit()
            return
        row.asked_state = "sending"
        db.commit()                                     # recorded BEFORE the send
        try:
            zconnect.send(row.customer_uid, row.phone, body)
        except client.ZuperError as e:
            row.asked_state, row.state = "failed", "done"
            row.error = client.sentence(e)[:500]
            registry.alert_failure(db, c.KEY, "Zuper did not send a text: %s" % row.error, now)
            db.commit()
            continue
        row.asked_state, row.state = "sent", "asked"
        db.commit()
        out["asked"] += 1
        _note(row, "asked the customer to rate the repair 1-5:\n\"%s\"" % body)


# ------------------------------------------------------------------------- 3. read replies

def _text(db: Session, row: ReviewRequest, body: str, now: datetime) -> bool:
    texts = list(row.texts or [])
    texts.append({"at": now.isoformat(), "body": body, "state": "sending"})
    row.texts = texts
    db.commit()                                         # recorded BEFORE the send
    try:
        zconnect.send(row.customer_uid, row.phone, body)
        texts[-1] = dict(texts[-1], state="sent")
        ok = True
    except client.ZuperError as e:
        texts[-1] = dict(texts[-1], state="failed")
        row.error = client.sentence(e)[:500]
        registry.alert_failure(db, c.KEY, "Zuper did not send a text: %s" % row.error, now)
        ok = False
    row.texts = list(texts)
    db.commit()
    return ok


def read_reply(db: Session, text: str, now: datetime) -> tuple[int | None, bool, str]:
    """(score, leak, quote) - in code when it can be, else by the AI."""
    score, leak = rules.parse_score(text), rules.mentions_leak(text)
    if score is not None or leak:
        return score, leak, text.strip()[:300]
    if registry.level(db, c.KEY, "ai_read") == rcat.NEVER:
        return None, False, ""
    try:
        prov, model = dai.provider_for(db, now, entry=c.KEY)
        turn = prov.complete(system=READ_SYSTEM, tools=[], model=model, max_tokens=REASONING_ROOM,
                             messages=[{"role": "user", "content": text[:1500]}])
    except (dai.Unavailable, providers.ProviderError) as e:
        log.info("a review reply was not read: %s", e)
        return None, False, ""
    dai._record(db, "reviews", model, turn)
    m = re.search(r"\{.*\}", turn.text or "", re.DOTALL)
    try:
        got = json.loads(m.group(0)) if m else {}
    except ValueError:
        got = {}
    quote = " ".join(str(got.get("quote") or "").split())[:300]
    if quote and not quote_ok(quote, text):
        return None, False, ""                          # an invented quote: a person reads it
    s = got.get("score")
    s = s if isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5 else None
    return s, bool(got.get("leak")) and bool(quote), quote


def _signal(db: Session, row: ReviewRequest, tag: str, quote: str, at: datetime) -> None:
    try:
        with db.begin_nested():
            db.add(ConversationSignal(
                source="zuper_connect", source_ref="rv:%d" % row.id, kind="text", at=at,
                phone=row.phone, inbound=True, job_uid=row.job_uid, job_number=row.job_number,
                board=row.board, customer_name=row.customer_name, technician=row.technician,
                tag=tag, quote=quote or "(rating %s)" % row.score))
            db.flush()
    except IntegrityError:
        pass


def _item(db: Session, row: ReviewRequest, now: datetime, *, unclear: bool = False) -> None:
    who = row.customer_name or "A customer"
    job = " (job #%s)" % row.job_number if row.job_number else ""
    if unclear:
        title = "%s answered the review text - read it%s" % (who, job)
        todo = "Read the reply and answer the customer."
    elif row.leak:
        title = "%s says it is STILL LEAKING%s" % (who, job)
        todo = "Call the customer and book a revisit."
    else:
        title = "%s rated the repair %s of 5%s" % (who, row.score, job)
        todo = "Call the customer: find out what went wrong and fix it."
    why = "“%s” - %s. %s" % ((row.reply or "")[:300], dc.local(row.replied_at or now).strftime(
        "%b %d %I:%M %p"), "Technician %s." % row.technician if row.technician else "")
    item = DispatchItem(key="rating:%d" % row.id, kind=KIND, queue=QUEUE, state="open",
                        created_at=now, job_uid=row.job_uid, job_number=row.job_number,
                        board=row.board, phone=row.phone, title=title[:300], why=why, todo=todo,
                        urgent=row.leak or (row.score or 5) <= 2, due_at=now,
                        evidence={"review_request_id": row.id, "score": row.score})
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError:
        return
    row.item_id = item.id
    alerts.fire(db, [drules.Event(key=item.key, kind=KIND, title=item.title, body=why,
                                  job_uid=row.job_uid, urgent=item.urgent)],
                ring=True, boards={row.job_uid or "": row.board or ""})


def handle_reply(db: Session, row: ReviewRequest, m: arules.ZMsg, s: c.Settings,
                 now: datetime, out: dict) -> None:
    body = (m.body or "").strip()
    if arules.is_stop(body):
        row.state, row.reason = "done", "the customer texted STOP"
        return
    if row.state == "improve":
        row.improve = body[:2000]
        row.state, row.reason = "done", "answered what to improve"
        if row.item_id:
            item = db.get(DispatchItem, row.item_id)
            if item is not None:
                item.why = (item.why or "") + "\nWhat to improve: “%s”" % body[:500]
        if _text(db, row, rules.offer_text(row.customer_name, s.review_link), now):
            row.link_sent = bool(s.review_link)
        _note(row, "the customer said what to improve: \"%s\"" % body[:500])
        return
    score, leak, quote = read_reply(db, body, now)
    row.reply, row.replied_at, row.score, row.leak = body[:2000], m.at, score, leak
    out["replies"] += 1
    if score is None and not leak:
        row.state, row.reason = "needs_person", "the reply was not a clear rating"
        _item(db, row, now, unclear=True)
        return
    if score == c.MAX_SCORE and not leak:
        _signal(db, row, "praise", quote, m.at)
        row.state, row.reason = "done", "rated 5"
        if _text(db, row, rules.five_text(row.customer_name, s.review_link), now):
            row.link_sent = bool(s.review_link)
        _note(row, "rated the repair 5 of 5%s." % (" - review link sent" if row.link_sent
                                                   else ""))
        return
    out["low"] += 1
    if leak:
        _signal(db, row, "still_leaking", quote, m.at)
    elif (score or 5) <= 2:
        _signal(db, row, "complaint", quote, m.at)
    row.state, row.reason = "improve", "rated %s" % (score or "low (a leak)")
    _item(db, row, now)
    _text(db, row, rules.improve_text(row.customer_name), now)
    _note(row, "rated the repair %s - the office was alerted. \"%s\"" % (
        "%s of 5" % score if score else "(no number)", body[:300]))


def read(db: Session, s: c.Settings, now: datetime, out: dict, convs: dict) -> None:
    for row in db.scalars(select(ReviewRequest).where(
            ReviewRequest.state.in_(c.OPEN_STATES), ReviewRequest.asked_state == "sent")):
        uid = convs.get(row.phone or "")
        if not uid:
            continue
        seen = set(row.seen_uids or [])
        asked = dc.aware(row.asked_at)
        for m in zconnect.messages(uid):
            if m.out or m.kind != "text" or m.at <= asked or m.uid in seen:
                continue
            if not c.call_hours(now):
                break                                  # answered tomorrow from 8 AM
            seen.add(m.uid)
            row.seen_uids = sorted(seen)
            db.commit()                                # this reply is handled ONCE
            handle_reply(db, row, m, s, now, out)
            db.commit()
            if row.state not in c.OPEN_STATES:
                break


# ------------------------------------------------------------------------- 4. no reply

def no_reply(db: Session, s: c.Settings, now: datetime, out: dict) -> None:
    lv = registry.level(db, c.KEY, "place_calls")
    for row in db.scalars(select(ReviewRequest).where(
            ReviewRequest.state == "asked", ReviewRequest.asked_state == "sent")):
        asked = dc.aware(row.asked_at)
        if now >= asked + timedelta(days=c.GIVE_UP_DAYS):
            row.state, row.reason = "no_reply", "no answer in %d days" % c.GIVE_UP_DAYS
            continue
        if row.call_state or now < asked + timedelta(days=s.no_reply_days):
            continue
        if lv == rcat.NEVER:
            row.call_state = "skipped"
            continue
        if not c.call_hours(now):
            continue
        row.call_at = now
        if lv == rcat.SHADOW:
            row.call_state = "shadow"
            continue
        if not crmlink.configured():
            row.call_state = "skipped"
            continue
        row.call_state = "requested"
        db.commit()                                    # recorded BEFORE the request
        r = crmlink.agent_call("+1" + row.phone, s.call_agent, CALL_REASON,
                               from_number=crmlink.current().from_number,
                               first_name=rules.first_name(row.customer_name),
                               requested_by="CRM reviews & QC")
        if not r.ok:
            row.call_state, row.error = "failed", r.reason[:500]
            registry.alert_failure(db, c.KEY, "The phone system did not place Anisa's call: %s"
                                   % r.reason, now)
            continue
        row.call_state = "called"
        out["calls"] += 1
        _note(row, "no answer to the rating text, so Anisa (AI) is calling the customer.")


# ------------------------------------------------------------------------- the pass

def run(db: Session, now: datetime | None = None) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"planned": 0, "skipped": 0, "asked": 0, "replies": 0, "low": 0, "calls": 0}
    if not zconfig.reviews_enabled():
        return dict(out, stopped=zconfig.REVIEWS_OFF_SENTENCE)
    if ai_engine.settings(db).paused:
        return dict(out, stopped="All AI agents are paused (Settings -> AI Connections).")
    s = settings(db)
    start = started_at(db, now)
    convs: dict | None = None
    live = registry.level(db, c.KEY, "send_text") == rcat.AUTO
    if live and db.scalar(select(ReviewRequest.id).where(
            ReviewRequest.state.in_(("planned", *c.OPEN_STATES))).limit(1)):
        try:
            convs = zconnect.conversations_by_phone()
        except client.ZuperError as e:
            registry.alert_failure(db, c.KEY, "Zuper's inbox could not be read: %s"
                                   % client.sentence(e), now)
            convs = None
    steps = [lambda: plan(db, s, now, start, out)]
    if convs is not None:
        steps.append(lambda: read(db, s, now, out, convs))
    steps += [lambda: ask(db, s, now, out, convs), lambda: no_reply(db, s, now, out)]
    for step in steps:
        try:
            step()
            db.commit()
        except Exception:
            db.rollback()
            log.exception("a reviews step failed")
    return out


def active_on(db: Session, phone: str, now: datetime) -> bool:
    """Is this agent mid-conversation with this number? The auto-reply leaves it alone then."""
    if not phone:
        return False
    return bool(db.scalar(select(ReviewRequest.id).where(
        ReviewRequest.phone == phone, ReviewRequest.state.in_(c.OPEN_STATES),
        ReviewRequest.asked_at >= now - timedelta(days=c.GIVE_UP_DAYS)).limit(1)))


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.reviews_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("reviews pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="reviews", daemon=True)
    t.start()
    return t
