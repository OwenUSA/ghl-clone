"""Texts sent when a job MOVES into a column (`config.STAGE_TEXTS`), each one Owen's own narrow
lift of the no-automatic-texts rule: "your inspection report was submitted to AHS" and "AHS
authorized the repair" (2026-10-09). Each has its own off | test | on (`<kind>_mode`).

How a move is seen. `dispatch_jobs` holds each Zuper job's column NOW; `reminder_stage_watch`
holds the column it was in at the previous pass. A job whose column went from anything else into
one of a text's columns has moved in — so does a job seen for the first time already sitting
there (created straight into it, or moved over from another board), once that text is live.

What is never texted:
  * jobs already in a text's columns when that text is switched on, or after a gap
    (`STAGE_WATCH_STALE_MINUTES`): its first pass only records where every job is
    (`reminder_settings.stage_watch_kinds` says when each text last ran);
  * the same job twice for the same text: the key is the kind and the job, whichever of its
    columns the job lands in and however often it goes back and forth;
  * at night: a move outside 8 AM-8 PM is held ("waiting") and sent at the first pass from 8 AM
    only if the job is still in those columns or further along (`config.still_true`);
    otherwise it is "cancelled" with the reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppointmentReminder, DispatchJob, ReminderSettings, ReminderStageWatch
from . import config as c
from . import rules


def key_for(kind: str, job_uid: str) -> str:
    return "%s:%s" % (kind, job_uid)


def mode_of(s: ReminderSettings, kind: str) -> str:
    return getattr(s, "%s_mode" % kind, None) or "off"


def active_kinds(s: ReminderSettings) -> list[str]:
    return [k for k in c.STAGE_KINDS if mode_of(s, k) in ("test", "on")]


def _recent(value, now: datetime) -> bool:
    if not value:
        return False
    at = rules.aware(datetime.fromisoformat(value) if isinstance(value, str) else value)
    return now - at <= timedelta(minutes=c.STAGE_WATCH_STALE_MINUTES)


def moves(db: Session, s: ReminderSettings, now: datetime) -> list[tuple[str, DispatchJob]]:
    """(kind, job) for every job that moved into an active text's columns since the last pass;
    brings the watch up to date. The caller commits."""
    kinds = active_kinds(s)
    if not kinds:
        return []
    stamps = dict(s.stage_watch_kinds) if s.stage_watch_kinds is not None else (
        # Written before per-kind times existed: the watch's own time stood for every text.
        {k: rules.aware(s.stage_watch_at).isoformat() for k in kinds if s.stage_watch_at})
    rows_live = _recent(s.stage_watch_at, now)
    live = [k for k in kinds if rows_live and _recent(stamps.get(k), now)]
    boards = {c.STAGE_TEXTS[k].board for k in kinds}
    seen = {w.job_uid: w for w in db.scalars(select(ReminderStageWatch))}
    out = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.board.in_(boards))):
        w = seen.get(j.job_uid)
        for k in live:
            was_in = w is not None and c.in_columns(k, w.board, w.status)
            if j.is_open and c.in_columns(k, j.board, j.status) and not was_in:
                out.append((k, j))
        if w is None:
            w = ReminderStageWatch(job_uid=j.job_uid)
            db.add(w)
        w.board, w.status, w.seen_at = j.board, j.status, now
    s.stage_watch_at = now
    s.stage_watch_kinds = {**stamps, **{k: now.isoformat() for k in kinds}}
    db.flush()
    return out


def held(db: Session) -> list[AppointmentReminder]:
    return list(db.scalars(select(AppointmentReminder).where(
        AppointmentReminder.kind.in_(c.STAGE_KINDS), AppointmentReminder.state == "waiting")))


def job_of(db: Session, job_uid: str) -> DispatchJob | None:
    return db.scalar(select(DispatchJob).where(DispatchJob.job_uid == job_uid))
