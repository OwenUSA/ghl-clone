"""One pass of the post-inspection report, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"built", "failed", "stopped"?}

Gates: the server gate INSPECTION_REPORT_ENABLED, "Pause all AI agents", the registry (its
vision action at Never, a spend limit). Then for every inspection whose pictures have been
quiet for `quiet_minutes` (test jobs only while "test only"): ONE report row is recorded first
(unique per job and visit day), the pictures are copied into the CRM, the vision model writes
the draft, and a Dispatch item + the bell say it is ready to review. A failure is kept as
"failed" with its reason and one alert; it is never retried by itself ("Rebuild" does that).
Nothing is written to Zuper and nothing is sent to anyone.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ai import engine as ai_engine
from ..dispatch import alerts
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..inspection_docs import reader as ireader
from ..models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    DispatchItem,
    DispatchJob,
    InspectionReport,
)
from ..registry import service as registry
from ..zuper import config as zconfig
from . import config as c
from . import photos as ph
from . import rules, vision

log = logging.getLogger("inspection_report")
KIND = "report_ready"
QUEUE = "after_visit"


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
                            new_value="first pass; inspections before this get no report"))
    db.commit()
    return now


def closed_gate(db: Session, now: datetime) -> str | None:
    if not zconfig.inspection_report_enabled():
        return zconfig.INSPECTION_REPORT_OFF_SENTENCE
    if ai_engine.settings(db).paused:
        return "All AI agents are paused (Settings -> AI Connections)."
    held = registry.level_refusal(db, c.KEY, "ai_vision")
    if held:
        return held
    over = registry.cap_refusal(db, c.KEY, now)
    if over:
        registry.alert_failure(db, c.KEY, over, now, what="cap_reached")
        return over
    return None


def _key(j: DispatchJob, at: datetime) -> str:
    return "%s|%s" % (j.job_uid, dc.local(at).date().isoformat())


def _language(db: Session, j: DispatchJob) -> str:
    """The customer's language (a person's choice, else what they wrote and said), read only."""
    try:
        from ..reminders import language
        phone = next((p for p in (j.phones or []) if p), None)
        return language.for_phone(db, phone, save=False) if phone else "en"
    except Exception:  # noqa: BLE001 - English when in doubt, never a failed report
        return "en"


def _written(db: Session, j: DispatchJob, visit_at: datetime) -> str:
    """The job's written findings: its fields, checklist answers and notes (read only)."""
    _ev, sources = ireader.gather(db, j, visit_at, live=False)
    lines = ["%s: %s" % (k, v) for k, v in sorted((j.fields or {}).items())
             if v and str(v).strip() and not str(v).startswith("http")]
    lines += ["[%s] %s" % (label, text) for label, text in sources]
    return "\n".join(lines)[:6000]


def build(db: Session, r: InspectionReport, j: DispatchJob, s: c.Settings,
          now: datetime) -> str:
    """Fill a report row: pictures, vision, draft, Dispatch item. -> "built" | "failed"."""
    try:
        cands = ph.fetch(ph.listed(j, dc.aware(r.visit_at)), s.max_candidates)
        if not cands:
            raise vision.Unavailable("no picture of the visit could be downloaded")
        res = vision.write(db, s, [x.ai_jpeg for x in cands],
                           _written(db, j, dc.aware(r.visit_at)), r.language or "en")
    except vision.Unavailable as e:
        r.state, r.error, r.updated_at = "failed", str(e)[:500], now
        registry.alert_failure(db, c.KEY, "a report could not be built: %s" % e, now)
        return "failed"
    d = res.draft
    r.candidates = [{"sha": x.sha, "taken_at": x.taken_at.isoformat() if x.taken_at else None,
                     "question": x.question} for x in cands]
    r.photos = [{"sha": cands[i].sha, "caption": cap,
                 "taken_at": cands[i].taken_at.isoformat() if cands[i].taken_at else None}
                for i, cap in d.photos]
    r.title, r.summary, r.findings = d.title or None, d.summary or None, d.findings
    r.model, r.input_tokens, r.output_tokens = res.model, res.input_tokens, res.output_tokens
    r.cost_micros = res.cost_micros
    r.state, r.error, r.updated_at = "draft", None, now
    _item(db, r, now)
    return "built"


def _item(db: Session, r: InspectionReport, now: datetime) -> None:
    key = "%s:%s" % (KIND, r.key)
    title = "Inspection report ready to review: #%s %s" % (r.job_number or "?",
                                                           (r.customer_name or "").strip())
    why = "%d picture%s picked, %d finding%s. Review it, edit it, then approve it." % (
        len(r.photos or []), "" if len(r.photos or []) == 1 else "s",
        len(r.findings or []), "" if len(r.findings or []) == 1 else "s")
    item = db.scalar(select(DispatchItem).where(DispatchItem.key == key))
    if item is None:
        item = DispatchItem(key=key, kind=KIND, queue=QUEUE, state="open", created_at=now,
                            job_uid=r.job_uid, job_number=r.job_number, board=r.board,
                            title=title[:300], why=why,
                            todo="Open Dispatch -> Reports, check the pictures and the words, "
                                 "then Approve. Nothing is sent to the customer.",
                            urgent=False, due_at=dc.add_business_minutes(now, 120),
                            evidence={"report_id": r.id})
        db.add(item)
        db.flush()
        alerts.fire(db, [drules.Event(key=key, kind=KIND, title=title, body=why,
                                      job_uid=r.job_uid)],
                    ring=True, boards={r.job_uid: r.board or ""})
    r.item_id = item.id


def close_item(db: Session, r: InspectionReport, now: datetime, note: str) -> None:
    item = db.get(DispatchItem, r.item_id) if r.item_id else None
    if item is not None and item.state == "open":
        item.state, item.closed_at, item.close_note = "resolved", now, note


def run(db: Session, now: datetime | None = None) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"built": 0, "failed": 0}
    gate = closed_gate(db, now)
    if gate:
        db.commit()
        return dict(out, stopped=gate)
    s = settings(db)
    start = started_at(db, now)
    floor = max(start, now - timedelta(days=c.LOOKBACK_DAYS))
    jobs = {j.job_uid: j for j in db.scalars(select(DispatchJob))}
    for r in db.scalars(select(InspectionReport).where(InspectionReport.state == "queued")):
        j = jobs.get(r.job_uid)       # "Rebuild": the same row, built again
        if j is None:
            r.state, r.error = "failed", "the job is no longer in Zuper's copy"
        else:
            r.state = "building"
            db.commit()
            out[build(db, r, j, s, now)] += 1
        db.commit()
    done = set(db.scalars(select(InspectionReport.key)))
    for j in db.scalars(select(DispatchJob).where(DispatchJob.board.in_(s.boards))):
        if s.test_only and (j.job_number or "") not in s.test_jobs:
            continue
        last = dc.aware(j.visit_photo_at)
        if last is None:
            continue
        key = _key(j, last)
        if not rules.ready(kind=drules.visit_kind(j), photo_count=j.visit_photo_count or 0,
                           last_photo_at=last, now=now,
                           quiet=timedelta(minutes=s.quiet_minutes), started=floor,
                           already=key in done):
            continue
        r = InspectionReport(key=key, job_uid=j.job_uid, job_number=j.job_number,
                             customer_name=j.customer_name, address=j.address, board=j.board,
                             visit_at=last, technician=j.visit_photo_by or j.technician,
                             language=_language(db, j), state="building", created_at=now)
        try:
            with db.begin_nested():
                db.add(r)
                db.flush()
        except IntegrityError:
            continue
        db.commit()                              # recorded BEFORE any download or model call
        done.add(key)
        try:
            result = build(db, r, j, s, now)
        except Exception:
            db.rollback()
            log.exception("report for job %s failed", j.job_number)
            r = db.get(InspectionReport, r.id)
            if r is not None:
                r.state, r.error = "failed", "unexpected error; see the server log"
            result = "failed"
        out[result] += 1
        db.commit()
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.inspection_report_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("inspection report pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="inspection-report", daemon=True)
    t.start()
    return t
