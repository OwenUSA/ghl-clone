"""One reminder pass (the worker's Zuper thread, after the Dispatch pass). Never raises.

  1. Gates: ZUPER_REMINDERS_ENABLED, then the mode (off | test | on). Off = nothing read.
  2. The Dispatch copy of Zuper must be fresh (`config.STALE_MINUTES`), or nothing is sent.
  3. Every open job in a column that counts, with a visit in the next two days -> the
     reminders due now (`rules.due`), minus the keys already used.
  4. Too many at once (`MAX_PER_PASS`) -> nothing is sent; a person looks first.
  5. For each: the Zuper customer's mobile, the customer's language, the wording. In test mode a
     number not on the test list is recorded "would_send" and not texted.
  6. The decision row is COMMITTED as "sending" BEFORE the text is handed over, and is never
     retried: a crash between the two leaves a "sending" row, never a second text.
  7. The text goes through `automations.send_outbound` (a contact holds the number: DND is
     honoured, the text lands on their thread) or `send_outbound_to_number` (a number-only
     thread) — the same transport as a staff text.

`python -m app.reminders.service` prints what would go out today and tomorrow (job numbers,
kind, time, language, state — no names, no numbers) and sends nothing.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import automations, number_threads
from ..models import AppointmentReminder, DispatchJob, DispatchState, ReminderSettings
from . import config as c
from . import language, rules

log = logging.getLogger("reminders")

PREVIEW = "preview:"
DAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre")


def settings(db: Session) -> ReminderSettings:
    s = db.get(ReminderSettings, 1)
    if s is None:
        s = ReminderSettings(id=1, mode="off")
        db.add(s)
        db.flush()
    return s


def read_settings(db: Session) -> ReminderSettings:
    """The settings WITHOUT writing a row (a read must not take SQLite's write lock)."""
    return db.get(ReminderSettings, 1) or ReminderSettings(id=1, mode="off")


def template(s: ReminderSettings, kind: str, lang: str) -> str:
    custom = ((s.templates or {}).get(kind) or {}).get(lang)
    return custom or c.DEFAULT_TEMPLATES[kind][lang]


def clock(local: datetime) -> str:
    """"2 PM", "2:30 PM" — how a person says a time."""
    fmt = "%I %p" if local.minute == 0 else "%I:%M %p"
    return local.strftime(fmt).lstrip("0")


def when_words(start: datetime, lang: str, end: datetime | None = None) -> dict[str, str]:
    """{day, time, window} in New York time. The window is the visit's range — "from 2 PM to
    4 PM" — when Zuper has an end on the same day after the start; otherwise "at 2 PM"."""
    local = rules.aware(start).astimezone(c.TZ)
    stop = rules.aware(end).astimezone(c.TZ) if end else None
    time = clock(local)
    if lang == "es":
        day = "%s %d de %s" % (DAYS_ES[local.weekday()], local.day, MONTHS_ES[local.month - 1])
    else:
        day = "%s, %s %d" % (local.strftime("%A"), local.strftime("%B"), local.day)
    ranged = stop is not None and stop > local and stop.date() == local.date()
    if lang == "es":
        window = "de %s a %s" % (time, clock(stop)) if ranged else "a las %s" % time
    else:
        window = "from %s to %s" % (time, clock(stop)) if ranged else "at %s" % time
    return {"day": day, "time": time, "window": window}


def render(text: str, *, first_name: str | None, start: datetime, lang: str,
           end: datetime | None = None) -> str:
    words = when_words(start, lang, end)
    name = (first_name or "").strip()
    if not name:
        text = text.replace(" {first_name}", "")
    text = text.replace("{first_name}", name)
    for k, v in words.items():
        text = text.replace("{%s}" % k, v)
    return text


def recipient(job: DispatchJob) -> str | None:
    """The Zuper customer's mobile; failing that, their only number. Two numbers and no mobile:
    nobody — texting a landline or the wrong person is worse than no reminder."""
    if job.mobile:
        return job.mobile
    phones = [p for p in job.phones or [] if p]
    return phones[0] if len(phones) == 1 else None


def first_name(db: Session, job: DispatchJob, phone: str | None):
    contact = number_threads.contact_holding(db, phone) if phone else None
    if contact is not None and contact.first_name:
        return contact, contact.first_name.strip().split(" ")[0]
    name = (job.customer_name or "").strip()
    return contact, (name.split(" ")[0] if name else None)


def fresh(db: Session, now: datetime) -> bool:
    s = db.get(DispatchState, 1)
    last = rules.aware(s.last_success_at) if s else None
    return last is not None and now - last <= timedelta(minutes=c.STALE_MINUTES)


def candidates(db: Session, now: datetime) -> list[tuple[DispatchJob, rules.Due]]:
    jobs = db.scalars(select(DispatchJob).where(
        DispatchJob.is_open.is_(True),
        DispatchJob.board.in_(list(c.COLUMNS)),
        DispatchJob.scheduled_start > now - timedelta(minutes=1),
        DispatchJob.scheduled_start <= now + timedelta(days=2)))
    out = []
    for j in jobs:
        for d in rules.due(job_uid=j.job_uid, board=j.board, status=j.status,
                           is_open=j.is_open, scheduled_start=j.scheduled_start, now=now):
            out.append((j, d))
    return out


def _used(db: Session, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    return set(db.scalars(select(AppointmentReminder.key).where(
        AppointmentReminder.key.in_(keys))))


def _row(job: DispatchJob, d: rules.Due, key: str, **kw) -> AppointmentReminder:
    return AppointmentReminder(
        key=key, job_uid=job.job_uid, job_number=job.job_number, board=job.board,
        status=job.status, kind=d.kind, visit_start=d.visit_start, **kw)


def _outcome(reason: str) -> str:
    if reason.startswith("suppressed"):
        return "suppressed"
    if reason.startswith("refused"):
        return "refused"
    if reason.startswith("failed"):
        return "failed"
    return "sent"


def _send(db: Session, row: AppointmentReminder, contact) -> None:
    if contact is not None:
        ev, reason = automations.send_outbound(db, contact, row.body or "")
        row.contact_id = contact.id
    else:
        thread, _ = number_threads.thread_for_number(db, row.phone or "")
        ev, reason = automations.send_outbound_to_number(db, thread, row.body or "")
        row.number_thread_id = thread.id
    row.state = _outcome(reason)
    row.reason = None if row.state == "sent" else reason
    row.event_id = getattr(ev, "id", None)


def run(db: Session, now: datetime | None = None) -> dict:
    """One pass. Returns its counts; every failure is the heartbeat's error, never raised."""
    now = rules.aware(now or datetime.now(UTC))
    if not c.enabled():
        return {"off": "ZUPER_REMINDERS_ENABLED is not set"}
    s = read_settings(db)
    if s.mode not in ("test", "on"):
        return {"off": "mode is off"}
    last = rules.aware(s.last_run_at)
    if last is not None and now - last < timedelta(seconds=c.EVERY_SECONDS):
        return {"skipped": "ran %ds ago" % (now - last).total_seconds()}
    s = settings(db)
    s.last_run_at = now
    counts = {"due": 0, "sent": 0, "would_send": 0, "skipped": 0, "refused": 0,
              "suppressed": 0, "failed": 0}
    try:
        if not fresh(db, now):
            raise RuntimeError("Zuper's jobs have not been read for over %d minutes, so no "
                               "reminder was sent (is the Dispatch reader running?)."
                               % c.STALE_MINUTES)
        tests = {x for x in (s.test_numbers or []) if x}
        found = candidates(db, now)
        keys = [d.key for _, d in found] + [PREVIEW + d.key for _, d in found]
        used = _used(db, keys)
        todo = []
        for job, d in found:
            phone = recipient(job)
            preview = s.mode == "test" and phone not in tests
            key = PREVIEW + d.key if preview else d.key
            if key in used or d.key in used:
                continue
            todo.append((job, d, phone, key, preview))
        counts["due"] = len(todo)
        real = [t for t in todo if not t[4]]
        if len(real) > c.MAX_PER_PASS:
            raise RuntimeError("%d reminders were due at once (more than %d), so none was sent. "
                               "Check the boards in Zuper, then send or switch off."
                               % (len(real), c.MAX_PER_PASS))
        for job, d, phone, key, preview in todo:
            if phone is None:
                db.add(_row(job, d, key, state="skipped", finished_at=now,
                            reason="The Zuper customer has no mobile number."))
                db.commit()
                counts["skipped"] += 1
                continue
            lang = language.for_phone(db, phone)
            contact, name = first_name(db, job, phone)
            body = render(template(s, d.kind, lang), first_name=name, start=d.visit_start,
                          lang=lang, end=job.scheduled_end)
            if preview:
                db.add(_row(job, d, key, state="would_send", phone=phone, language=lang,
                            body=body, finished_at=now,
                            reason="Test mode: this number is not on the test list."))
                db.commit()
                counts["would_send"] += 1
                continue
            row = _row(job, d, key, state="sending", phone=phone, language=lang, body=body)
            db.add(row)
            db.commit()                 # recorded BEFORE the text leaves; never retried
            try:
                _send(db, row, contact)
            except Exception as exc:
                db.rollback()
                row = db.get(AppointmentReminder, row.id)
                row.state, row.reason = "failed", "Not retried: %s" % exc
                log.exception("reminder %s failed", key)
            row.finished_at = now
            db.commit()
            counts[row.state] = counts.get(row.state, 0) + 1
        s = settings(db)
        s.last_success_at, s.last_error, s.last_counts = now, None, counts
        db.commit()
    except Exception as exc:
        db.rollback()
        s = settings(db)
        s.last_run_at = now
        s.last_error, s.last_error_at = str(exc), now
        db.commit()
        if not isinstance(exc, RuntimeError):
            log.exception("reminder pass failed")
        counts["error"] = str(exc)
    return counts


def plan(db: Session, now: datetime | None = None) -> list[dict]:
    """What the reminders would do for every visit today and tomorrow — read only, no names
    and no numbers. Used by the dry run and the Settings screen."""
    now = rules.aware(now or datetime.now(UTC))
    s = read_settings(db)
    out = []
    jobs = db.scalars(select(DispatchJob).where(
        DispatchJob.is_open.is_(True),
        DispatchJob.scheduled_start > now,
        DispatchJob.scheduled_start <= now + timedelta(days=2)).order_by(
        DispatchJob.scheduled_start))
    for j in jobs:
        counts = c.column_counts(j.board, j.status)
        if not counts and j.board not in c.COLUMNS:
            continue
        phone = recipient(j)
        row = {"job_uid": j.job_uid, "job_number": j.job_number, "board": j.board,
               "status": j.status, "visit_start": rules.aware(j.scheduled_start).isoformat(),
               "counts": counts, "has_mobile": phone is not None,
               "language": language.for_phone(db, phone, save=False) if phone else None,
               "reminders": []}
        for kind, window in rules.windows(j.scheduled_start).items():
            key = rules.key_for(j.job_uid, j.scheduled_start, kind)
            done = db.scalar(select(AppointmentReminder).where(
                AppointmentReminder.key.in_([key, PREVIEW + key])))
            row["reminders"].append({
                "kind": kind,
                "from": window[0].isoformat() if window else None,
                "until": window[1].isoformat() if window else None,
                "state": done.state if done else (
                    "never" if not window or not counts else
                    "missed" if now >= window[1] else "waiting"),
            })
        out.append(row)
    if s.mode == "off":
        for r in out:
            for rem in r["reminders"]:
                if rem["state"] == "waiting":
                    rem["state"] = "waiting (switched off)"
    return out


def main() -> None:
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        s = read_settings(db)
        print("Server gate ZUPER_REMINDERS_ENABLED: %s · mode: %s" % (
            "on" if c.enabled() else "OFF", s.mode))
        print("Dispatch copy fresh: %s" % ("yes" if fresh(db, datetime.now(UTC)) else "NO"))
        for r in plan(db):
            local = datetime.fromisoformat(r["visit_start"]).astimezone(c.TZ)
            print("#%-6s %-22s %-28s %s  %s  %s" % (
                r["job_number"] or "?", (r["board"] or "")[:22], (r["status"] or "")[:28],
                local.strftime("%a %b %d %I:%M %p"), r["language"] or "-",
                " · ".join("%s: %s" % (x["kind"], x["state"]) for x in r["reminders"])
                if r["counts"] else "column does not count"))
        print("DRY RUN: nothing was sent and nothing was written.")
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
