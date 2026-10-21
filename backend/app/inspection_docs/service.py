"""One pass of the inspection-documentation agent, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"checked", "documented", "flagged", "resolved", "texted", "called",
                     "stopped"?}

Gates: the server gate INSPECTION_DOCS_ENABLED, "Pause all AI agents". Then:
  1. every visit finished since the agent's start (the Dispatch reader's photo-quiet signal, or
     the job reaching Inspection Completed / Repair Complete) on a chosen board, older than the
     grace period, opens ONE check (test jobs only while "test only" is on);
  2. each open check gathers the evidence (reader.py), asks the AI only for written details
     still missing (extract.py), and decides (rules.py);
  3. complete -> "documented"; missing -> a Dispatch item + the bell, a text to the technician
     (its registry level), and after the recheck wait a call by the technician agent (its
     level, Never by default); found later -> the item resolves itself.
It writes NOTHING to Zuper. A text or a call is recorded before it is sent, never retried.
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
from ..dispatch import alerts
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..followup.config import call_hours
from ..models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    DispatchItem,
    DispatchJob,
    InspectionCheck,
)
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import config as zconfig
from . import config as c
from . import extract, reader, rules

log = logging.getLogger("inspection_docs")
KIND = "missing_docs"
QUEUE = "after_visit"


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def started_at(db: Session, now: datetime) -> datetime:
    """When the agent first ran on this server - recorded once in the registry log. Visits that
    finished before it are never checked (no backlog)."""
    at = db.scalar(select(AgentRegistryLog.at).where(
        AgentRegistryLog.entry_key == c.KEY, AgentRegistryLog.what == "started")
        .order_by(AgentRegistryLog.at))
    if at is not None:
        return dc.aware(at)
    db.add(AgentRegistryLog(at=now, entry_key=c.KEY, what="started",
                            new_value="first pass; visits before this are not checked"))
    db.commit()
    return now


def closed_gate(db: Session) -> str | None:
    if not zconfig.inspection_docs_enabled():
        return zconfig.INSPECTION_DOCS_OFF_SENTENCE
    if ai_engine.settings(db).paused:
        return "All AI agents are paused (Settings -> AI Connections)."
    return None


def visit_at(j: DispatchJob, now: datetime) -> tuple[str, datetime] | None:
    """(inspection | repair, when the visit finished), or None."""
    kind = drules.visit_kind(j)
    done = drules.visit_done_at(j, now)
    stage = dc.aware(j.status_since) if (
        (kind == "inspection" and j.status in dc.AFTER_INSPECTION_STAGES) or
        (kind == "repair" and j.status in dc.AFTER_REPAIR_STAGES)) else None
    at = max([t for t in (done, stage) if t], default=None)
    return (kind, at) if at else None


def technician(j: DispatchJob) -> str | None:
    return j.visit_photo_by or j.technician or ((j.assigned or [None])[0])


def _key(j: DispatchJob, kind: str, at: datetime) -> str:
    return "%s|%s|%s" % (j.job_uid, kind, dc.local(at).date().isoformat())


def _open(db: Session, j: DispatchJob, kind: str, at: datetime, s: c.Settings
          ) -> InspectionCheck | None:
    tech = technician(j)
    row = InspectionCheck(key=_key(j, kind, at), job_uid=j.job_uid, job_number=j.job_number,
                          board=j.board, customer_name=j.customer_name, kind=kind, visit_at=at,
                          technician=tech, tech_phone=s.phone_for(tech), state="waiting")
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    return row


def _found_out(ev: rules.Evidence) -> dict:
    return {k: {"value": f.value, "source": f.source, "quote": f.quote}
            for k, f in ev.details.items()}


def _texts(row: InspectionCheck, missing: list[str], ev: rules.Evidence) -> tuple[str, str, str]:
    what = "inspection" if row.kind == "inspection" else "repair"
    label = "#%s %s" % (row.job_number or "?", row.customer_name or "")
    title = ("%s - %s details missing: %s" % (label.strip(), what, rules.words(missing)))[:300]
    lines = ["The %s visit finished %s. Still missing: %s." % (
        what, dc.local(row.visit_at).strftime("%b %d, %I:%M %p"), rules.words(missing))]
    for k, f in ev.details.items():
        lines.append("Found %s (%s): %s" % (rules.LABEL[k], f.source, f.value))
    lines.append("Photos: %d. Videos: %d." % (ev.photos, ev.videos))
    who = row.technician or "the technician"
    todo = ("Ask %s for %s and add it to job #%s in Zuper." % (
        who, rules.words(missing), row.job_number or "?"))
    return title, "\n".join(lines), todo


def _flag(db: Session, row: InspectionCheck, missing: list[str], ev: rules.Evidence,
          now: datetime) -> None:
    title, why, todo = _texts(row, missing, ev)
    item = db.get(DispatchItem, row.item_id) if row.item_id else None
    if item is None:
        key = "%s:%s" % (KIND, row.key)
        item = db.scalar(select(DispatchItem).where(DispatchItem.key == key))
        if item is None:
            item = DispatchItem(key=key, kind=KIND, queue=QUEUE, state="open", created_at=now,
                                job_uid=row.job_uid, job_number=row.job_number, board=row.board,
                                title=title, why=why, todo=todo, urgent=False,
                                due_at=dc.add_business_minutes(now, 60))
            db.add(item)
            db.flush()
            alerts.fire(db, [drules.Event(key=key, kind=KIND, title=title, body=why,
                                          job_uid=row.job_uid)],
                        ring=True, boards={row.job_uid: row.board or ""})
        row.item_id = item.id
    item.title, item.why, item.todo, item.updated_at = title, why, todo, now
    item.evidence = {"missing": missing, "found": _found_out(ev), "photos": ev.photos,
                     "videos": ev.videos, "technician": row.technician}
    row.state = "flagged"


def _resolve(db: Session, row: InspectionCheck, now: datetime) -> None:
    row.state = "resolved"
    item = db.get(DispatchItem, row.item_id) if row.item_id else None
    if item is not None and item.state == "open":
        item.state, item.closed_at = "resolved", now
        item.close_note = "Everything is documented now."


def _text_tech(db: Session, row: InspectionCheck, missing: list[str], now: datetime) -> None:
    lv = registry.level(db, c.KEY, "text_staff")
    row.text_at = now
    if lv == rcat.NEVER:
        row.text_state = "skipped"
        return
    if not row.tech_phone:
        row.text_state, row.note = "skipped", "no phone for %s in the settings" % (
            row.technician or "the technician")
        return
    what = "inspection" if row.kind == "inspection" else "repair"
    row.text_body = ("Dream Team Roofing office: job #%s (%s) - after your %s we're missing %s. "
                     "Please add it in Zuper or reply here." % (
                         row.job_number or "?", (row.customer_name or "").strip() or "customer",
                         what, rules.words(missing)))[:320]
    if lv == rcat.SHADOW:
        row.text_state = "shadow"
        return
    if not crmlink.configured():
        row.text_state, row.note = "skipped", "the phone system link is not configured"
        return
    row.text_state = "sending"
    db.commit()
    r = crmlink.send_sms("+1" + row.tech_phone, row.text_body)
    if r.ok:
        row.text_state = "sent"
    else:
        row.text_state, row.note = "failed", r.reason[:500]
        registry.alert_failure(db, c.KEY, "the text to the technician failed: %s" % r.reason,
                               now)


def _call_tech(db: Session, row: InspectionCheck, missing: list[str], s: c.Settings,
               now: datetime) -> None:
    lv = registry.level(db, c.KEY, "call_staff")
    row.call_at = now
    if lv == rcat.NEVER:
        row.call_state = "skipped"
        return
    if not row.tech_phone:
        row.call_state = "skipped"
        return
    if lv == rcat.SHADOW:
        row.call_state = "shadow"
        return
    if not crmlink.configured():
        row.call_state, row.note = "skipped", "the phone system link is not configured"
        return
    if not call_hours(now):
        row.call_at = None                          # outside 8 AM-8 PM Mon-Sat: next pass
        return
    row.call_state = "requested"
    db.commit()
    reason = "job #%s %s: we are missing %s" % (row.job_number or "?", row.kind,
                                                rules.words(missing))
    first = (row.technician or "").split(" ")[0] or None
    r = crmlink.agent_call("+1" + row.tech_phone, s.call_agent, reason,
                           from_number=s.caller_id, first_name=first,
                           requested_by="CRM inspection documentation")
    if r.ok:
        row.call_ref = str((r.data or {}).get("call_id") or "")[:64] or None
    else:
        row.call_state, row.note = "failed", r.reason[:500]
        registry.alert_failure(db, c.KEY, "the call to the technician failed: %s" % r.reason,
                               now)


def check(db: Session, row: InspectionCheck, j: DispatchJob, s: c.Settings, now: datetime,
          *, live: bool = True) -> str:
    asked = dc.aware(row.text_at) if row.text_state in ("sent", "shadow") else None
    ev, sources = reader.gather(db, j, dc.aware(row.visit_at), tech_phone=row.tech_phone,
                                asked_at=asked, live=live)
    need = [m for m in rules.missing(row.kind, ev) if m in rules.DETAILS]
    wanted = need + (["couldnt_inspect"] if row.kind == "inspection" and
                     "couldnt_inspect" not in ev.details else [])
    if wanted and sources and registry.level(db, c.KEY, "ai_read") != rcat.NEVER:
        found, why = extract.find(db, sources, wanted, now)
        for k, f in found.items():
            ev.add(k, f)
        if why:
            row.note = why[:500]
    missing = rules.missing(row.kind, ev)
    row.missing, row.found = missing, _found_out(ev)
    row.photos, row.videos, row.checked_at = ev.photos, ev.videos, now
    if not missing:
        if row.state == "flagged":
            _resolve(db, row, now)
            return "resolved"
        row.state = "documented"
        return "documented"
    _flag(db, row, missing, ev, now)
    if row.text_state is None:
        _text_tech(db, row, missing, now)
        return "texted" if row.text_state in ("sent", "shadow") else "flagged"
    if row.call_state is None and row.text_at and \
            now - dc.aware(row.text_at) >= timedelta(minutes=s.recheck_minutes):
        _call_tech(db, row, missing, s, now)
        return "called" if row.call_state in ("requested", "shadow") else "flagged"
    return "flagged"


def run(db: Session, now: datetime | None = None, *, live: bool = True) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"checked": 0, "documented": 0, "flagged": 0, "resolved": 0, "texted": 0,
           "called": 0}
    gate = closed_gate(db)
    if gate:
        return dict(out, stopped=gate)
    s = settings(db)
    start = started_at(db, now)
    floor = max(start, now - timedelta(days=c.LOOKBACK_DAYS))
    jobs = {j.job_uid: j for j in db.scalars(select(DispatchJob).where(
        DispatchJob.board.in_(s.boards)))}
    for j in jobs.values():
        if s.test_only and (j.job_number or "") not in s.test_jobs:
            continue
        found = visit_at(j, now)
        if not found:
            continue
        kind, at = found
        if at < floor or now - at < timedelta(minutes=s.grace_minutes):
            continue
        if _open(db, j, kind, at, s) is not None:
            db.commit()
    for row in db.scalars(select(InspectionCheck).where(
            InspectionCheck.state.in_(("waiting", "flagged")))):
        j = jobs.get(row.job_uid)
        if j is None or now - dc.aware(row.visit_at) > timedelta(days=c.EXPIRE_DAYS):
            row.state = "expired"
            db.commit()
            continue
        if now - dc.aware(row.visit_at) < timedelta(minutes=s.grace_minutes):
            continue
        out["checked"] += 1
        row.updated_at = now
        try:
            result = check(db, row, j, s, now, live=live)
        except Exception:
            db.rollback()
            log.exception("inspection check failed on job %s", row.job_number)
            continue
        if result in out:
            out[result] += 1
        if result in ("texted", "called"):
            out["flagged"] += 1
        db.commit()
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.inspection_docs_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("inspection documentation pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="inspection-docs", daemon=True)
    t.start()
    return t
