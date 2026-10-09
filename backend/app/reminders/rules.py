"""PURE: which reminders a job is due right now. No database, no clock — `now` is passed in.

A reminder's key is the job, the visit's start (UTC) and the kind. A rescheduled visit is a new
key, so it gets its own reminders; moving it back to the old time finds the old key already
used and sends nothing twice.

Times are New York's (`config.TZ`), daylight saving included.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import config as c


@dataclass(frozen=True)
class Due:
    kind: str
    key: str
    visit_start: datetime      # UTC


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def key_for(job_uid: str, start: datetime, kind: str) -> str:
    return "%s:%s:%s" % (job_uid, aware(start).strftime("%Y-%m-%dT%H:%M:%SZ"), kind)


def _quiet(local: datetime) -> bool:
    return not (c.QUIET_UNTIL <= local.time() < c.QUIET_FROM)


def quiet(now: datetime) -> bool:
    """Outside 8 AM-8 PM New York — no text goes out."""
    return _quiet(aware(now).astimezone(c.TZ))


def windows(start: datetime) -> dict[str, tuple[datetime, datetime] | None]:
    """When each reminder may go out, as (from, until) in UTC; None = never for this visit."""
    start = aware(start)
    local = start.astimezone(c.TZ)
    eve = (local - timedelta(days=1)).date()
    day_from = datetime.combine(eve, c.DAY_BEFORE_FROM, tzinfo=c.TZ)
    day_until = datetime.combine(eve, c.QUIET_FROM, tzinfo=c.TZ)
    out: dict[str, tuple[datetime, datetime] | None] = {
        c.DAY_BEFORE: (day_from.astimezone(UTC), day_until.astimezone(UTC))}
    at = start - timedelta(hours=c.FOUR_HOUR_HOURS)
    latest = start - timedelta(hours=c.FOUR_HOUR_LATEST_HOURS)
    # A visit before noon has no 4-hour text: it would land before 8 AM (the day-before text
    # covers it). Due at start - 4 h, and still sent by a late pass until start - 2 h, but
    # never inside the quiet hours.
    out[c.FOUR_HOUR] = None if _quiet(at.astimezone(c.TZ)) else (at, latest)
    return out


def due(*, job_uid: str, board: str | None, status: str | None, is_open: bool,
        scheduled_start: datetime | None, now: datetime) -> list[Due]:
    """The reminders that may be sent at `now` for this job (the caller drops keys already
    used). Nothing for a closed job, a job with no time, a column that does not count, a visit
    already started, or inside the quiet hours."""
    if not is_open or scheduled_start is None or not job_uid:
        return []
    if not c.column_counts(board, status):
        return []
    now = aware(now)
    start = aware(scheduled_start)
    if start <= now or _quiet(now.astimezone(c.TZ)):
        return []
    out = []
    for kind, window in windows(start).items():
        if window and window[0] <= now < window[1]:
            out.append(Due(kind, key_for(job_uid, start, kind), start))
    return out
