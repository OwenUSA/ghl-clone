"""One pass of the insights agent, every hour on its own worker thread.

    run(db, now) -> {"read", "tags", "alerts", "resolved", "snapshot", "stopped"?}

Off unless INSIGHTS_ENABLED (the report itself works regardless - it is computed on demand).
  1. the tagger reads every new call and text once (registry `ai_read`);
  2. each "still leaking", "come back" or complaint becomes ONE Dispatch item in "Customer
     issues" + the bell, with the quote, the job and the technician; a still-leaking item
     resolves itself once a future visit is booked on that job;
  3. once a New York day, yesterday's KPIs are kept as a snapshot (trends).
Nothing is sent to anyone and nothing is written to Zuper.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import engine as ai_engine
from ..dispatch import alerts
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..models import (
    AgentRegistryEntry,
    ConversationSignal,
    DispatchItem,
    DispatchJob,
    InsightSnapshot,
)
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import config as zconfig
from . import config as c
from . import metrics, tagger

log = logging.getLogger("insights")
KIND = "customer_issue"
QUEUE = "issues"
WHAT = {"still_leaking": "says it is STILL LEAKING", "callback_request": "asked us to come back",
        "complaint": "complained"}


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def _future_visit(db: Session, job_uid: str | None, now: datetime) -> bool:
    if not job_uid:
        return False
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == job_uid))
    start = dc.aware(j.scheduled_start) if j is not None else None
    return bool(start and start > now)


def raise_alerts(db: Session, now: datetime, out: dict) -> None:
    since = now - timedelta(days=c.LOOKBACK_DAYS)
    for s in db.scalars(select(ConversationSignal).where(
            ConversationSignal.tag.in_(c.ALERT_TAGS), ConversationSignal.at >= since)):
        key = "issue:%d" % s.id
        item = db.scalar(select(DispatchItem).where(DispatchItem.key == key))
        booked = s.tag == "still_leaking" and _future_visit(db, s.job_uid, now)
        if item is None:
            if booked:
                continue
            who = s.customer_name or ("%s-%s-%s" % (s.phone[:3], s.phone[3:6], s.phone[6:])
                                      if s.phone else "A customer")
            title = ("%s %s%s" % (who, WHAT[s.tag], " (job #%s)" % s.job_number
                                  if s.job_number else ""))[:300]
            why = "“%s” - %s %s, %s." % (
                s.quote, "call" if s.kind == "call" else "text",
                dc.local(s.at).strftime("%b %d %I:%M %p"),
                "technician %s" % s.technician if s.technician else "no technician on record")
            item = DispatchItem(key=key, kind=KIND, queue=QUEUE, state="open", created_at=now,
                                job_uid=s.job_uid, job_number=s.job_number, board=s.board,
                                phone=s.phone, title=title, why=why,
                                todo="Call the customer back and book a revisit if needed."
                                if s.tag != "complaint" else "Call the customer back.",
                                urgent=s.tag == "still_leaking", due_at=now,
                                evidence={"signal_id": s.id, "tag": s.tag})
            db.add(item)
            db.flush()
            alerts.fire(db, [drules.Event(key=key, kind=KIND, title=title, body=why,
                                          job_uid=s.job_uid, urgent=s.tag == "still_leaking")],
                        ring=True, boards={s.job_uid or "": s.board or ""})
            out["alerts"] += 1
        elif booked and item.state == "open":
            item.state, item.closed_at = "resolved", now
            item.close_note = "A visit is booked."
            out["resolved"] += 1


def snapshot(db: Session, now: datetime, out: dict) -> None:
    yesterday = dc.local(now).date() - timedelta(days=1)
    if db.scalar(select(InsightSnapshot.id).where(InsightSnapshot.day == yesterday)):
        return
    rep = metrics.compute(db, yesterday, yesterday)
    db.add(InsightSnapshot(day=yesterday, values=rep["tiles"]))
    out["snapshot"] = yesterday.isoformat()


def run(db: Session, now: datetime | None = None) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"read": 0, "tags": 0, "alerts": 0, "resolved": 0, "snapshot": None}
    if not zconfig.insights_enabled():
        return dict(out, stopped=zconfig.INSIGHTS_OFF_SENTENCE)
    if ai_engine.settings(db).paused:
        return dict(out, stopped="All AI agents are paused (Settings -> AI Connections).")
    s = settings(db)
    hourly = s.tag_hourly or dc.local(now).hour == c.NIGHT_HOUR
    if hourly and registry.level(db, c.KEY, "ai_read") != rcat.NEVER:
        res = tagger.tag(db, now, s)
        out["read"], out["tags"] = res["read"], res["tags"]
        if res.get("stopped"):
            out["stopped"] = res["stopped"]
            registry.alert_failure(db, c.KEY, "conversations were not read: %s"
                                   % res["stopped"], now)
    for step in (lambda: raise_alerts(db, now, out), lambda: snapshot(db, now, out)):
        try:
            step()
            db.commit()
        except Exception:
            db.rollback()
            log.exception("an insights step failed")
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.insights_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("insights pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="insights", daemon=True)
    t.start()
    return t
