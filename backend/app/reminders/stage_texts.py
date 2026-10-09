"""Texts sent when a job MOVES into a column (2026-10-09): "your inspection report was
submitted to AHS". Owen's second lift of the no-automatic-texts rule — this one text only.

How a move is seen. `dispatch_jobs` holds each Zuper job's column NOW; `reminder_stage_watch`
holds the column it was in at the previous pass. A job whose column went from anything else into
"Submit To AHS…" or "Awaiting AHS Decision" on AHS - Inspection has moved in — so does a job seen
for the first time already sitting there (created straight into it, or moved over from another
board), once the watch is live.

What is never texted:
  * jobs already in those columns when the text is switched on, or after a gap in the watch
    (`STAGE_WATCH_STALE_MINUTES`): that pass only records where every job is;
  * the same job twice: the key is the job alone, whichever of the two columns it lands in and
    however often it goes back and forth;
  * at night: a move outside 8 AM-8 PM is held ("waiting") and sent at the first pass from 8 AM
    only if the job is still in those columns or further along (`config.ahs_submitted_still_true`);
    otherwise it is "cancelled" with the reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppointmentReminder, DispatchJob, ReminderSettings, ReminderStageWatch
from . import config as c
from . import rules


def key_for(job_uid: str) -> str:
    return "%s:%s" % (c.AHS_SUBMITTED, job_uid)


def _watch_is_live(s: ReminderSettings, now: datetime) -> bool:
    last = rules.aware(s.stage_watch_at)
    return last is not None and now - last <= timedelta(minutes=c.STAGE_WATCH_STALE_MINUTES)


def moves(db: Session, s: ReminderSettings, now: datetime) -> list[DispatchJob]:
    """The jobs that moved into the "submitted" columns since the last pass, and the watch
    brought up to date. Writes the watch rows; the caller commits."""
    live = _watch_is_live(s, now)
    seen = {w.job_uid: w for w in db.scalars(select(ReminderStageWatch))}
    out = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.board == c.AHS_SUBMITTED_BOARD)):
        w = seen.get(j.job_uid)
        was_in = w is not None and c.ahs_submitted_column(w.board, w.status)
        now_in = j.is_open and c.ahs_submitted_column(j.board, j.status)
        if live and now_in and not was_in:
            out.append(j)
        if w is None:
            w = ReminderStageWatch(job_uid=j.job_uid)
            db.add(w)
        w.board, w.status, w.seen_at = j.board, j.status, now
    s.stage_watch_at = now
    db.flush()
    return out


def held(db: Session) -> list[AppointmentReminder]:
    return list(db.scalars(select(AppointmentReminder).where(
        AppointmentReminder.kind == c.AHS_SUBMITTED, AppointmentReminder.state == "waiting")))


def job_of(db: Session, job_uid: str) -> DispatchJob | None:
    return db.scalar(select(DispatchJob).where(DispatchJob.job_uid == job_uid))
