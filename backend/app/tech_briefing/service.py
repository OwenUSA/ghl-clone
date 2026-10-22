"""One pass of the field technician briefing, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"briefed", "skipped", "stopped"?}

Gates: the server gate TECH_BRIEFING_ENABLED, "Pause all AI agents", texts at Never. Then, once
it is past the send time (6 PM New York by default), for tomorrow: every technician with a visit
on a chosen board (the test technicians only while "test only") gets ONE briefing - recorded
first (unique per technician and day), built from the job fields, the inspection-documentation
agent's findings and, for jobs with no recorded tools, an AI suggestion clearly marked - and
texted to the phone in the Inspection documentation settings, or only recorded at Shadow.
Never retried; a job added after the briefing is not re-sent. Nothing goes to customers or Zuper.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import crmlink
from ..ai import engine as ai_engine
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..inspection_docs import service as idocs
from ..models import AgentRegistryEntry, DispatchJob, InspectionCheck, TechBriefing
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import config as zconfig
from . import config as c
from . import rules, suggest

log = logging.getLogger("tech_briefing")


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def closed_gate(db: Session) -> str | None:
    if not zconfig.tech_briefing_enabled():
        return zconfig.TECH_BRIEFING_OFF_SENTENCE
    if ai_engine.settings(db).paused:
        return "All AI agents are paused (Settings -> AI Connections)."
    return registry.level_refusal(db, c.KEY, "text_staff")


def _found(db: Session, job_uid: str) -> dict:
    row = db.scalars(select(InspectionCheck).where(InspectionCheck.job_uid == job_uid)
                     .order_by(InspectionCheck.id.desc()).limit(1)).first()
    return row.found or {} if row is not None else {}


def _in_test(tech: str, s: c.Settings) -> bool:
    t = tech.strip().lower()
    return any(t == x.strip().lower() or t.split()[0] == x.strip().lower().split()[0]
               for x in s.test_technicians if x.strip())


def zuper_link(job: DispatchJob) -> str | None:
    """The job in Zuper, the same link the Dispatch page draws (2026-10-22, the user: send each
    technician the link to every job); none when the deployment sets no link."""
    template = zconfig.job_url_template()
    return template.format(uid=job.job_uid) if template and job.job_uid else None


def brief(db: Session, tech: str, jobs: list[DispatchJob], day, s: c.Settings,
          now: datetime) -> str:
    key = "%s|%s" % (tech.strip().lower(), day.isoformat())
    phone = idocs.settings(db).phone_for(tech)
    row = TechBriefing(key=key, technician=tech, phone=phone, brief_date=day,
                       job_numbers=[j.job_number for j in jobs], state="building",
                       created_at=now)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return "already"
    db.commit()                                  # recorded BEFORE anything is sent
    lines = [rules.job_line(j, _found(db, j.job_uid), drules.visit_kind(j),
                            link=zuper_link(j)) for j in jobs]
    need = [{"job_number": ln.job_number, **ln.facts} for ln in lines if ln.needs_tools]
    if need and registry.level(db, c.KEY, "ai_suggest") != rcat.NEVER:
        tips, meta = suggest.suggest(db, need, now)
        row.model, row.input_tokens, row.output_tokens = (
            meta["model"], meta["input_tokens"], meta["output_tokens"])
        row.note = meta["note"]
        if tips:
            lines = [rules.job_line(j, _found(db, j.job_uid), drules.visit_kind(j),
                                    tips.get(j.job_number or ""), link=zuper_link(j))
                     for j in jobs]
    parts = rules.texts(tech, day, [ln.text for ln in lines], c.MAX_TEXT)
    row.body = "\n\n".join(parts)
    lv = registry.level(db, c.KEY, "text_staff")
    if lv == rcat.SHADOW:
        row.state = "shadow"
        return "briefed"
    if not phone:
        row.state = "skipped"
        row.note = "no phone for %s in Inspection documentation settings" % tech
        return "skipped"
    if not crmlink.configured():
        row.state, row.note = "skipped", "the phone system link is not configured"
        return "skipped"
    row.state = "sending"
    db.commit()
    for part in parts:
        r = crmlink.send_sms("+1" + phone, part)
        if not r.ok:
            row.state, row.note = "failed", r.reason[:500]
            registry.alert_failure(db, c.KEY, "the briefing to %s failed: %s" % (tech, r.reason),
                                   now)
            return "skipped"
    row.state, row.sent_at = "sent", now
    return "briefed"


def run(db: Session, now: datetime | None = None) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"briefed": 0, "skipped": 0}
    gate = closed_gate(db)
    if gate:
        return dict(out, stopped=gate)
    s = settings(db)
    day = rules.due(now, *s.hour_minute())
    if day is None:
        return out
    jobs = list(db.scalars(select(DispatchJob).where(DispatchJob.board.in_(s.boards))))
    for tech, mine in rules.by_technician(jobs, day, skip_stages=s.skip_stages).items():
        if s.test_only and not _in_test(tech, s):
            continue
        try:
            result = brief(db, tech, mine, day, s, now)
        except Exception:
            db.rollback()
            log.exception("briefing for %s failed", tech)
            continue
        if result in out:
            out[result] += 1
        db.commit()
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.tech_briefing_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("technician briefing pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="tech-briefing", daemon=True)
    t.start()
    return t
